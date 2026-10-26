"""Run with: python -m uvicorn fns_parser.api:app --host 127.0.0.1 --port 8000."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated

from fastapi import FastAPI, Path as ApiPath, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .api_models import ApiError, HealthResponse, ParserResult
from .identifiers import validate_identifier
from .service import parse_company
from .jobs import JobError, JobStore
from .manual_api import router as manual_router
from .batch_captcha_api import router as batch_captcha_router
from .captcha_queue import CaptchaQueue, default_db
from .credit.api import router as credit_router
from .credit.questions import CreditError
from .credit.store import SessionStore
from .scoring.api import router as scoring_router
from .scoring.service import ScoringService

logger = logging.getLogger(__name__)
HTTP_STATUS = {"ok": 200, "partial": 200, "not_found": 404, "error": 502}


class ApiSettings(BaseModel):
    download_dir: Path = Path("data/extracts")
    timeout: float = Field(20.0, gt=0, allow_inf_nan=False)
    wait_timeout: float = Field(60.0, gt=0, allow_inf_nan=False)
    max_concurrent: int = Field(2, ge=1)
    cors_origins: list[str] = Field(default_factory=list)
    captcha_timeout: float = Field(120, gt=0, le=600, allow_inf_nan=False)
    job_retention: float = Field(900, gt=0, le=86400, allow_inf_nan=False)
    max_jobs: int = Field(32, ge=1, le=128)
    credit_db: Path = Path("data/credit.sqlite3")
    captcha_db: Path = Field(default_factory=default_db)
    scoring_cache_dir: Path = Path('data/scoring_cache')
    scoring_timeout: float = Field(45.0, gt=0, le=120, allow_inf_nan=False)
    scoring_offline: bool = False
    scoring_source_revision: str = Field('main', pattern=r'^(?:main|[0-9a-f]{40})$')
    scoring_max_report_age_days: int = Field(730, ge=1, le=3650)

    @classmethod
    def from_env(cls):
        return cls(
            download_dir=os.getenv("FNS_DOWNLOAD_DIR", "data/extracts"),
            timeout=os.getenv("FNS_TIMEOUT", "20"),
            wait_timeout=os.getenv("FNS_WAIT_TIMEOUT", "60"),
            max_concurrent=os.getenv("FNS_MAX_CONCURRENT", "2"),
            cors_origins=[origin.strip() for origin in os.getenv("FNS_CORS_ORIGINS", "").split(",") if origin.strip()],
            captcha_timeout=os.getenv("FNS_CAPTCHA_TIMEOUT", "120"),
            job_retention=os.getenv("FNS_JOB_RETENTION", "900"),
            max_jobs=os.getenv("FNS_MAX_JOBS", "32"),
            credit_db=os.getenv("CREDIT_DB_PATH", "data/credit.sqlite3"),
            scoring_cache_dir=os.getenv('SCORING_CACHE_DIR', 'data/scoring_cache'),
            scoring_timeout=os.getenv('SCORING_TIMEOUT', '45'),
            scoring_offline=os.getenv('SCORING_OFFLINE', 'false'),
            scoring_source_revision=os.getenv('SCORING_RFSD_REVISION', 'main'),
            scoring_max_report_age_days=os.getenv('SCORING_MAX_REPORT_AGE_DAYS', '730'),
        )


def error_response(status_code, code, message, *, details=None, headers=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"status": "error", "error": error}, status_code=status_code,
                        headers={"Cache-Control": "no-store", **(headers or {})})


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    slots = BoundedSemaphore(settings.max_concurrent)
    jobs = JobStore(slots, captcha_timeout=settings.captcha_timeout,
                    retention=settings.job_retention, max_jobs=settings.max_jobs)

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.close()

    app = FastAPI(
        title="Кредитная анкета и открытые данные бизнеса",
        version="0.4.0",
        lifespan=lifespan,
        description=("Публичные данные ЕГРЮЛ/ЕГРИП и «Прозрачного бизнеса». "
                     "Дополнительный источник — ГИР БО. Кредитная анкета по дереву от 23.10.2026; "
                     "общий JSON с приоритетом подтверждённых данных реестра над ответами. "
                     "По умолчанию краткая карточка, full=true — полный ответ, enrich=false — только ФНС. "
                     "Все денежные суммы в рублях. Один ИНН может вернуть несколько регистраций ИП."),
    )
    app.state.settings = settings
    app.state.jobs = jobs
    app.state.captcha_queue = CaptchaQueue(settings.captcha_db)
    app.state.credit_sessions = SessionStore(settings.credit_db)
    app.state.scoring = ScoringService(settings)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["GET", "POST", "DELETE"], allow_headers=["Accept", "Content-Type"],
                           expose_headers=["Retry-After"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
        return error_response(422, "invalid_request", "Некорректные параметры запроса", details=details)

    @app.exception_handler(JobError)
    async def job_error(request: Request, exc: JobError):
        return error_response(exc.status, exc.code, str(exc),
                              headers={"Retry-After": "5"} if exc.status == 503 else None)

    @app.exception_handler(CreditError)
    async def credit_error(request: Request, exc: CreditError):
        return error_response(exc.status, exc.code, str(exc))

    app.include_router(manual_router(jobs, settings, lambda *args, **kwargs: parse_company(*args, **kwargs)))
    app.include_router(batch_captcha_router(app.state.captcha_queue, settings))
    app.include_router(credit_router(app.state.credit_sessions, jobs, settings))
    app.include_router(scoring_router(app.state.scoring, jobs, settings, app.state.credit_sessions,
                                     lambda *args, **kwargs: parse_company(*args, **kwargs)))

    @app.get("/health", response_model=HealthResponse, tags=["Сервис"],
             summary="Проверка работы API без обращения к ФНС")
    async def health():
        return {"status": "ok"}

    @app.get(
        "/api/v1/companies/{identifier}",
        response_model=ParserResult,
        response_model_exclude_unset=True,
        tags=["Бизнес"],
        summary="Получить данные по ИНН, ОГРН или ОГРНИП",
        description=("Новый запрос к ФНС; PDF сохраняются на сервере. "
                     "HTTP 200 со status=partial означает частичный результат: проверяйте sources, public_sources и issues. "
                     "Отсутствующие поля не означают ноль. Запрос может занять более минуты."),
        responses={
            404: {"model": ParserResult, "description": "Оба источника ответили, совпадений нет"},
            422: {"model": ApiError, "description": "Неверный идентификатор или параметр full"},
            500: {"model": ApiError, "description": "Ошибка сохранения файлов или внутренняя ошибка"},
            502: {"model": ParserResult, "description": "Не удалось получить данные от ФНС"},
            503: {"model": ApiError, "description": "Все слоты парсера заняты; повторите позже",
                  "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Секунды до повторной попытки"}}},
        },
    )
    def company(
        identifier: Annotated[str, ApiPath(max_length=64, description="ИНН: 10/12 цифр; ОГРН: 13; ОГРНИП: 15", examples=["4028051108"])],
        response: Response,
        full: Annotated[bool, Query(description="Полные исходные данные вместо краткой карточки")] = False,
        enrich: Annotated[bool, Query(description="Подключить ГИР БО к данным ФНС")] = True,
        max_pages: Annotated[int, Query(ge=1, le=10, description="Лимит страниц поиска ГИР БО; неполная выдача отмечается явно")] = 3,
    ):
        # A synchronous FastAPI route runs in a worker thread, not the event loop.
        try:
            identifier, _ = validate_identifier(identifier)
        except ValueError as exc:
            return error_response(422, "invalid_identifier", str(exc))
        if not slots.acquire(blocking=False):
            return error_response(503, "busy", "Парсер занят. Повторите запрос позже.", headers={"Retry-After": "5"})
        try:
            result = parse_company(identifier, output_dir=settings.download_dir, full=full,
                                   timeout=settings.timeout, wait_timeout=settings.wait_timeout,
                                   enrich=enrich, max_pages=max_pages)
            response.status_code = HTTP_STATUS[result["status"]]
            response.headers["Cache-Control"] = "no-store"
            return result
        except OSError:
            logger.exception("Не удалось сохранить файлы парсера")
            return error_response(500, "file_error", "Не удалось сохранить файлы на сервере")
        except Exception:
            logger.exception("Ошибка обработки запроса парсером")
            return error_response(500, "internal_error", "Внутренняя ошибка парсера")
        finally:
            slots.release()

    frontend_dir = Path(os.getenv("ALFA_FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend")))
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
