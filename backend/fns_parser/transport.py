"""Cookie sessions, bounded retries and explicit upstream errors."""

import http.client
import http.cookiejar
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import re


def check_html_challenge(raw: bytes):
    """Detect actual challenge pages, not a site's unused captcha templates."""
    text = raw.decode("utf-8", errors="replace").lower()
    if ("qauth_show_captcha" in text or "qrator captcha" in text
            or re.search(r'id\s*=\s*[\"\'](?:captcha-popup|ncapcha)[\"\']', text)):
        raise SourceError("captcha_required", "Источник требует ручного прохождения капчи")


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}


def check_payload(data):
    if not isinstance(data, dict):
        return data
    errors = data.get("ERRORS") or data.get("errors")
    error_text = json.dumps(errors, ensure_ascii=False) if errors else ""
    # FNS also returns captchaRequired as a hint for the *next* operation
    # alongside an already accepted request id/token. Do not discard that job.
    accepted = any(data.get(key) for key in ("id", "t"))
    if (data.get("captchaRequired") is True and not accepted) or "captcha" in error_text.lower():
        raise SourceError("captcha_required", "ФНС требует ручного ввода капчи")
    if "ratelimit" in error_text.lower():
        raise SourceError("rate_limited", "ФНС ограничила частоту запросов")
    if errors or data.get("ERROR") or data.get("error"):
        raise SourceError("upstream_error", str(errors or data.get("ERROR") or data.get("error"))[:1200])
    return data


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or old.hostname != new.hostname:
            raise SourceError("unexpected_redirect", "ФНС перенаправила запрос на другой адрес или HTTP")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Session:
    def __init__(self, base_url: str, *, timeout=20.0, min_interval=0.35, retries=2):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout должен быть положительным конечным числом")
        self.base_url = base_url
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.last_request = 0.0
        self.opener = urllib.request.build_opener(
            SameHostRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def fetch(self, path: str, data: dict | None = None, *, max_bytes=32 * 1024 * 1024,
              json_body: dict | None = None, headers: dict | None = None) -> bytes:
        url = urllib.parse.urljoin(self.base_url, path)
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(self.base_url).netloc:
            raise SourceError("invalid_url", "Запрос за пределы выбранного сервиса запрещён")
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        if json_body is not None:
            if data is not None:
                raise ValueError("data и json_body несовместимы")
            body = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        # Retrying a POST can enqueue the same job twice. Retry only GETs.
        attempts = self.retries + 1 if body is None else 1
        for attempt in range(attempts):
            time.sleep(max(0, self.min_interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            req = urllib.request.Request(url, data=body, headers={
                "User-Agent": "fns-parser/0.1 (+public-registry-client)",
                "Accept": "application/json, application/pdf, text/html, */*",
                "Referer": self.base_url + "index.html",
                **({"Content-Type": "application/json"} if json_body is not None else {}),
                **(headers or {}),
            })
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    content = response.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise SourceError("response_too_large", f"Ответ источника превышает {max_bytes} байт")
                check_html_challenge(content)
                return content
            except urllib.error.HTTPError as exc:
                raw = exc.read(8192)
                check_html_challenge(raw)
                try:
                    check_payload(json.loads(raw))
                except (ValueError, UnicodeError):
                    pass
                if exc.code == 429:
                    raise SourceError("rate_limited", "HTTP 429: источник ограничил частоту запросов") from exc
                if exc.code in (401, 403, 451):
                    raise SourceError("access_restricted", f"Источник ограничил доступ: HTTP {exc.code}") from exc
                if exc.code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.75 * 2**attempt)
                    continue
                raise SourceError("http_error", f"Источник вернул HTTP {exc.code}") from exc
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.75 * 2**attempt)
                    continue
                raise SourceError("network_error", f"Не удалось связаться с источником: {exc}") from exc
        raise AssertionError("unreachable")

    def json(self, path: str, data: dict | None = None):
        raw = self.fetch(path, data)
        try:
            def invalid_constant(value):
                raise ValueError(f"Invalid JSON constant: {value}")
            payload = json.loads(raw, parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise SourceError("invalid_response", "ФНС вернула не JSON (возможно, изменился сайт)") from exc
        return check_payload(payload)


def poll(fetch, ready, *, wait_timeout=60.0, interval=1.5, pause_clock=None):
    deadline = time.monotonic() + wait_timeout
    while True:
        paused_before = pause_clock() if pause_clock else 0
        payload = fetch()
        if pause_clock:
            deadline += pause_clock() - paused_before
        if ready(payload):
            return payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("poll_timeout", "ФНС не подготовила результат за отведённое время")
        time.sleep(min(interval, remaining))


def require_token(data, key="t") -> str:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise SourceError("invalid_response", f"В ответе ФНС отсутствует поле {key}")
    return value
