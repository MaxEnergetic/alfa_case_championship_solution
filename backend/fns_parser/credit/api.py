"""Stateful questionnaire endpoints mounted in the parser's existing FastAPI app."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from ..api_models import ApiError

from .dialogue import submit
from .models import AttachRegistry, SessionView, SubmitAnswer
from .questions import CreditError
from .registry import attach
from .result import view


def router(store, jobs, settings):
    def guard(request: Request, response: Response):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/") and origin not in settings.cors_origins:
            raise CreditError(403, "origin_not_allowed", "Запрос с этого сайта не разрешён")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"

    routes = APIRouter(prefix="/api/v1/credit/sessions", tags=["Кредитная анкета"], dependencies=[Depends(guard)],
                       responses={403: {"model": ApiError}, 404: {"model": ApiError},
                                  409: {"model": ApiError}, 422: {"model": ApiError}})

    @routes.post("", status_code=201, response_model=SessionView, response_model_exclude_unset=True,
                 summary="Создать анкету и получить первый вопрос")
    def create():
        return view(store.create())

    @routes.get("/{session_id}", response_model=SessionView, response_model_exclude_unset=True,
                summary="Общий JSON: компания, анкета, следующий вопрос и подбор")
    def get(session_id: str):
        return view(store.get(session_id))

    @routes.post("/{session_id}/answers", response_model=SessionView, response_model_exclude_unset=True,
                 summary="Ответить или исправить ответ; исправление сбрасывает последующие ответы")
    def answer(session_id: str, body: SubmitAnswer):
        record = store.update(session_id, body.revision, lambda r: submit(r, body.question_id, body.value))
        return view(record)

    @routes.post("/{session_id}/registry", response_model=SessionView, response_model_exclude_unset=True,
                 summary="Присоединить завершённое задание парсера по ИНН / ОГРН",
                 description="Передайте job_id из /api/v1/jobs. При нескольких регистрациях выберите selected_ogrn. "
                 "Совпадающие по смыслу подтверждённые поля парсера имеют приоритет над анкетой.")
    def registry(session_id: str, body: AttachRegistry):
        record = store.update(session_id, body.revision,
                              lambda r: attach(r, jobs.get(body.job_id), body.selected_ogrn))
        return view(record)

    @routes.get("/{session_id}/result", response_model=SessionView, response_model_exclude_unset=True,
                summary="Получить итоговый общий JSON после всех вопросов")
    def result(session_id: str):
        payload = view(store.get(session_id))
        if payload.state != "ready":
            raise CreditError(409, "questionnaire_incomplete", "Сначала ответьте на оставшиеся вопросы")
        return payload

    @routes.delete("/{session_id}", status_code=204, summary="Удалить сохранённую анкету")
    def delete(session_id: str, revision: Annotated[int, Query(ge=0)]):
        store.delete(session_id, revision)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return routes
