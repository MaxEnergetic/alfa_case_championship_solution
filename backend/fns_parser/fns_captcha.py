"""FNS image challenge protocol; only a human supplies the answer.

The official captcha-dialog.html forms submit captcha + captchaToken to
captcha-proc.json. Its result is the authorization token for the interrupted
request (pbCaptchaToken, vyp3CaptchaToken, or r for an EGRUL PDF request).
"""
import re
import time
from io import BytesIO
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from .html_tools import Tree
from .manual_captcha import CaptchaImage
from .transport import SourceError, poll

SOURCES = {"egrul": "https://egrul.nalog.ru/", "transparent_business": "https://pb.nalog.ru/"}


def raster_image(content):
    if not isinstance(content, bytes) or len(content) > 1024 * 1024:
        raise SourceError("invalid_response", "Некорректный размер картинки ФНС")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"PNG", "JPEG", "GIF"} or not (1 <= image.width <= 2048 and 1 <= image.height <= 2048):
                raise ValueError("Invalid image")
            media_type = Image.MIME[image.format]
            image.verify()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise SourceError("invalid_response", "Не распознана картинка ФНС") from exc
    return CaptchaImage(content, media_type)


def challenge_form(raw):
    root = Tree(raw.decode("utf-8")).root
    form = next(root.find("form", id="frmCaptcha"), None)
    if form is None or form.attrs.get("action") != "captcha-proc.json" or form.attrs.get("method", "").lower() != "post":
        raise SourceError("invalid_response", "Изменилась форма капчи ФНС")
    fields = {n.attrs.get("name"): n.attrs.get("value", "") for n in form.find("input")}
    token = fields.get("captchaToken", "")
    if "captcha" not in fields or not re.fullmatch(r"[A-Za-z0-9_-]{16,2048}", token):
        raise SourceError("invalid_response", "В форме ФНС нет токена капчи")
    for img in form.find("img"):
        src = img.attrs.get("src", "")
        url = urlsplit(src)
        if not url.scheme and not url.netloc and url.path == "/static/captcha.bin" and parse_qs(url.query).get("a") == [token]:
            return token, src
    raise SourceError("invalid_response", "Картинка не соответствует форме капчи ФНС")


class FnsRequests:
    def __init__(self, session, source, handler=None):
        self.session, self.source, self.handler = session, source, handler
        self.identifier = ""
        self.waited = 0.0

    def json(self, path, data=None):
        try:
            return self.session.json(path, data)
        except SourceError as exc:
            if exc.code != "captcha_required" or self.handler is None:
                raise
        # Bounded manual retries; refresh never submits an invented answer.
        for attempt in range(1, 6):
            token, image_path = challenge_form(self.session.fetch("captcha-dialog.html"))
            image = raster_image(self.session.fetch(image_path, max_bytes=1024 * 1024))
            started = time.monotonic()
            try:
                action, code = self.handler(image=image, source=self.source, inn=self.identifier,
                                            source_url=SOURCES[self.source], attempt=attempt)
            finally:
                self.waited += time.monotonic() - started
            if action == "skip":
                raise SourceError("captcha_skipped", "Ручной ввод капчи ФНС отменён")
            if action == "refresh":
                continue
            if action != "answer" or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                raise SourceError("invalid_captcha_answer", "Капча ФНС содержит 6 цифр")
            try:
                ready = self.session.json("captcha-proc.json", {"captcha": code, "captchaToken": token})
                if not isinstance(ready, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,2048}", ready):
                    raise SourceError("invalid_response", "ФНС не вернула подтверждение ручного ввода")
                if self.source == "egrul" and urlsplit(path).path.startswith("vyp-request/"):
                    url = urlsplit(path)
                    query = dict(parse_qsl(url.query)); query["r"] = ready
                    retry_path = urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment))
                    return self.session.json(retry_path, data)
                field = "pbCaptchaToken" if self.source == "transparent_business" else "vyp3CaptchaToken"
                return self.session.json(path, {**(data or {}), field: ready})
            except SourceError as exc:
                if exc.code != "captcha_required":
                    raise
        raise SourceError("captcha_required", "ФНС не приняла ручной ввод за 5 попыток")

    def poll(self, fetch, ready, *, wait_timeout):
        return poll(fetch, ready, wait_timeout=wait_timeout, pause_clock=lambda: self.waited)
