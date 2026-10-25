"""Asynchronous scoring, sharing the existing job and manual CAPTCHA endpoints."""
from copy import deepcopy
from datetime import date
from threading import Event

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ..compact import compact_result
from ..credit.questions import CreditError
from ..credit.registry import selected_entity
from ..credit.result import view
from ..identifiers import validate_identifier
from ..jobs import JobError
from ..manual_api import JobView
from .models import StartScoring, SessionScoring
from .service import today, unavailable


def check_options(year, as_of):
    as_of = as_of or today()
    if as_of > today() or (year is not None and date(year, 12, 31) >= as_of):
        raise JobError(422, 'invalid_scoring_period', 'Дата оценки не должна быть в будущем; отчётный год должен завершиться до неё')
    return as_of


def legal_identifier(identifier):
    try:
        value, kind = validate_identifier(identifier)
    except ValueError as exc:
        raise JobError(422, 'invalid_identifier', str(exc)) from exc
    if kind not in ('inn_legal', 'ogrn'):
        raise JobError(422, 'unsupported_entity_type', 'Эта модель обучена на юридических лицах; ИП не поддерживаются')
    return value, kind


def selected_compact(result, identifier, kind):
    if result.get('schema_version') == '1.0':
        result = compact_result(result)
    if result.get('schema_version') != 'compact-1.0':
        raise JobError(422, 'not_registry_job', 'Нужен результат задания парсера ЕГРЮЛ/ПБ')
    matches = [e for e in result.get('entities', []) if e.get('inn' if kind == 'inn_legal' else 'ogrn') == identifier]
    if len(matches) != 1 or matches[0].get('entity_type') != 'legal':
        raise JobError(409, 'company_mismatch', 'Не удалось однозначно сопоставить юридическое лицо с запросом')
    return {**deepcopy(result), 'entities': [deepcopy(matches[0])]}


def router(service, jobs, settings, sessions, parse_company):
    def guard(request: Request, response: Response):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/') and origin not in settings.cors_origins:
            raise JobError(403, 'origin_not_allowed', 'Запрос с этого сайта не разрешён')
        response.headers['Cache-Control'] = 'no-store'

    routes = APIRouter(tags=['Финансовый скоринг'], dependencies=[Depends(guard)])

    @routes.get('/api/v1/scoring/model', summary='Версия, признаки, порог и доступность модели')
    def model():
        info = service.describe()
        return JSONResponse(info, status_code=200 if info['status'] == 'ready' else 503,
                            headers={'Cache-Control': 'no-store'})

    @routes.post('/api/v1/scoring/jobs', status_code=202, response_model=JobView,
                 response_model_exclude_none=True,
                 summary='ИНН/ОГРН → отчётность → признаки → финансовая устойчивость',
                 description='Результат и капча доступны по status_url в обычном API заданий. '
                 'ИНН: запрос RFSD без ФНС. ОГРН: сначала разрешение ИНН через парсер или registry_job_id.')
    def start(body: StartScoring):
        identifier, kind = legal_identifier(body.identifier)
        as_of = check_options(body.year, body.as_of)
        compact = None
        if body.registry_job_id:
            job = jobs.get(body.registry_job_id)
            if job['state'] != 'completed' or not job.get('result'):
                raise JobError(409, 'registry_not_ready', 'Дождитесь завершения задания парсера')
            compact = selected_compact(job['result'], identifier, kind)

        def work(handler):
            registry = compact
            if kind == 'ogrn' and registry is None:
                result = parse_company(identifier, output_dir=settings.download_dir, full=False,
                                       enrich=False, timeout=settings.timeout, wait_timeout=settings.wait_timeout,
                                       captcha_handler=handler)
                try:
                    registry = selected_compact(result, identifier, kind)
                except JobError:
                    failure = unavailable(None, as_of, 'registry_identity_unavailable',
                                          'Парсер не подтвердил ИНН для этого ОГРН')
                    failure['registry_sources'] = result.get('sources', {})
                    return failure
            entity = registry['entities'][0] if registry else {}
            inn = entity.get('inn') or identifier
            return service.score(inn, as_of=as_of, year=body.year, revision=body.revision,
                                 compact=registry, expected_ogrn=entity.get('ogrn'))

        return jobs.start(identifier, work)

    @routes.post('/api/v1/credit/sessions/{session_id}/scoring/jobs', status_code=202,
                 summary='Посчитать скоринг и сохранить его в общем JSON анкеты',
                 description='Передайте revision анкеты. После завершения получите свежую анкету: '
                 'scoring содержит результат. Изменённая во время расчёта анкета не получает устаревший результат.')
    def start_session(session_id: str, body: SessionScoring):
        as_of = check_options(body.year, body.as_of)
        gate = Event()
        launch = {}

        def begin(record):
            payload = view(record)
            inn = payload.data.company.inn
            if not inn:
                raise CreditError(409, 'inn_required', 'Сначала укажите ИНН в анкете')
            inn, kind = legal_identifier(inn)
            entity = selected_entity(record)
            registry = record.get('registry') or {}
            if registry.get('entities') and entity is None:
                raise CreditError(409, 'registration_selection_required', 'Сначала выберите ОГРН компании')
            compact = {**deepcopy(registry), 'entities': [deepcopy(entity)]} if entity else None

            def work(handler):
                if not gate.wait(10):
                    raise RuntimeError('Scoring session launch was not committed')
                result = service.score(inn, as_of=as_of, year=body.year, revision=body.source_revision,
                                       compact=compact, expected_ogrn=entity.get('ogrn') if entity else None)
                def finish(current):
                    if current.get('scoring_job_id') != launch['job']['job_id']:
                        raise CreditError(409, 'scoring_superseded', 'Уже запущен другой расчёт')
                    current['scoring'] = deepcopy(result)
                try:
                    saved = sessions.update(session_id, body.revision + 1, finish)
                    result['session_attachment'] = {'status': 'attached', 'session_id': session_id,
                                                    'revision': saved['revision']}
                except CreditError as exc:
                    if exc.status not in (404, 409):
                        raise
                    result['session_attachment'] = {'status': 'stale_or_deleted_session', 'session_id': session_id}
                return result

            launch['job'] = jobs.start(inn, work)
            record['scoring_job_id'] = launch['job']['job_id']
            record['scoring'] = None

        try:
            record = sessions.update(session_id, body.revision, begin)
        finally:
            gate.set()
        return {**launch['job'], 'session_id': session_id, 'session_revision': record['revision']}

    return routes
