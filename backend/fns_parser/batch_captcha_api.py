"""Human input for the separate batch collector; no upstream requests here."""
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from .jobs import JobError
from .manual_api import CaptchaAnswer, PRIVATE_HEADERS, STATIC


def router(queue, settings):
    routes = APIRouter()

    @routes.get('/captcha/batch', include_in_schema=False)
    def page():
        return FileResponse(STATIC / 'batch-captcha.html', headers={**PRIVATE_HEADERS,
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    @routes.get('/api/v1/batch/captchas', tags=['Ручная проверка'])
    def pending():
        return JSONResponse({'captchas': queue.pending()}, headers=PRIVATE_HEADERS)

    @routes.get('/api/v1/batch/captchas/{captcha_id}/image', tags=['Ручная проверка'])
    def image(captcha_id: str):
        content, media_type = queue.image(captcha_id)
        return Response(content, media_type=media_type, headers=PRIVATE_HEADERS)

    @routes.post('/api/v1/batch/captchas/{captcha_id}/answer', tags=['Ручная проверка'])
    def answer(captcha_id: str, body: CaptchaAnswer, request: Request):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/') and origin not in settings.cors_origins:
            raise JobError(403, 'origin_not_allowed', 'Запрос с этого сайта не разрешён')
        return JSONResponse(queue.answer(captcha_id, body.action, body.code), headers=PRIVATE_HEADERS)

    return routes
