"""Manual-check UI and job endpoints, mounted into the existing FastAPI app."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identifiers import validate_identifier
from .jobs import JobError

STATIC = Path(__file__).parent / "static"
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                   "X-Content-Type-Options": "nosniff"}


class StartJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=64, examples=["4028051108"])
    full: bool = False
    enrich: bool = True
    max_pages: int = Field(3, ge=1, le=10)


class CaptchaAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["answer", "refresh", "skip"] = "answer"
    code: str | None = Field(None, min_length=1, max_length=16, pattern=r"^[A-Za-z0-9]+$")

    @model_validator(mode="after")
    def require_code(self):
        if self.action == "answer" and self.code is None:
            raise ValueError("Введите код с картинки")
        if self.action != "answer" and self.code is not None:
            raise ValueError("Код передаётся только для action=answer")
        return self


class PendingCaptcha(BaseModel):
    id: str
    source: str
    inn: str
    source_url: str
    attempt: int
    expires_at: str
    image_url: str
    answer_url: str


class JobView(BaseModel):
    job_id: str
    identifier: str
    state: Literal["running", "waiting_for_captcha", "completed", "failed", "cancelled"]
    created_at: str
    status_url: str
    page_url: str
    finished_at: str | None = None
    captcha: PendingCaptcha | None = None
    result: dict | None = Field(None, description="Обычный краткий или полный ответ парсера после завершения задания")
    error: dict | None = None


class AnswerAccepted(BaseModel):
    status: Literal["accepted"]


def router(store, settings, runner):
    routes = APIRouter()

    def reply(content, status=200):
        return JSONResponse(content, status_code=status, headers=PRIVATE_HEADERS)

    def origin_guard(request):
        origin = request.headers.get("origin")
        own = str(request.base_url).rstrip("/")
        if origin and origin != own and origin not in settings.cors_origins:
            raise JobError(403, "origin_not_allowed", "Запрос с этого сайта не разрешён")

    @routes.get("/captcha", include_in_schema=False)
    def page():
        return FileResponse(STATIC / "captcha.html", headers={**PRIVATE_HEADERS,
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    @routes.get("/captcha/assets/{name}", include_in_schema=False)
    def asset(name: Literal["captcha.js", "captcha.css", "batch-captcha.js"]):
        return FileResponse(STATIC / name, headers=PRIVATE_HEADERS)

    @routes.post("/api/v1/jobs", tags=["Задания парсера"], status_code=202, response_model=JobView,
                 summary="Запустить сбор ЕГРЮЛ/ЕГРИП, ПБ и ГИР БО в фоне")
    def start(body: StartJob, request: Request):
        origin_guard(request)
        try:
            identifier, _ = validate_identifier(body.identifier)
        except ValueError as exc:
            return reply({"status": "error", "error": {"code": "invalid_identifier", "message": str(exc)}}, 422)

        def work(handler):
            return runner(identifier, output_dir=settings.download_dir, timeout=settings.timeout,
                          wait_timeout=settings.wait_timeout, full=body.full, enrich=body.enrich,
                          max_pages=body.max_pages, captcha_handler=handler)

        return reply(store.start(identifier, work), 202)

    @routes.get("/api/v1/jobs/{job_id}", tags=["Задания парсера"], response_model=JobView, summary="Состояние задания и итоговый JSON")
    def status(job_id: str):
        return reply(store.get(job_id))

    @routes.delete("/api/v1/jobs/{job_id}", tags=["Задания парсера"], response_model=JobView, summary="Отменить задание")
    def cancel(job_id: str, request: Request):
        origin_guard(request)
        return reply(store.cancel(job_id))

    @routes.get("/api/v1/jobs/{job_id}/captchas/{captcha_id}/image", tags=["Ручная проверка"], summary="Изображение для человека")
    def image(job_id: str, captcha_id: str):
        picture = store.image(job_id, captcha_id)
        return Response(picture.content, media_type=picture.media_type, headers=PRIVATE_HEADERS)

    @routes.post("/api/v1/jobs/{job_id}/captchas/{captcha_id}/answer", tags=["Ручная проверка"], response_model=AnswerAccepted,
                 summary="Передать введённый человеком код, запросить заново или пропустить")
    def answer(job_id: str, captcha_id: str, body: CaptchaAnswer, request: Request):
        origin_guard(request)
        return reply(store.answer(job_id, captcha_id, body.action, body.code))

    return routes
