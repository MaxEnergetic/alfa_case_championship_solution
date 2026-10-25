"""Adapters for the public web endpoints; no registration or API key."""

from urllib.parse import quote

from .transport import Session, SourceError, require_token
from .fns_captcha import FnsRequests

EGRUL_URL = "https://egrul.nalog.ru/"
PB_URL = "https://pb.nalog.ru/"


def matches(row: dict, query: str, *, pb=False) -> bool:
    keys = ("inn", "ogrn", "ogrnip") if pb else ("i", "o")
    return any(str(row.get(k, "")) == query for k in keys)


class EgrulClient:
    def __init__(self, session=None, *, timeout=20.0, wait_timeout=60.0, max_pages=20, captcha_handler=None):
        self.session = session or Session(EGRUL_URL, timeout=timeout)
        self.requests = FnsRequests(self.session, "egrul", captcha_handler)
        self.wait_timeout = wait_timeout
        self.max_pages = max_pages

    def search(self, query: str) -> list[dict]:
        self.requests.identifier = query
        self.session.fetch("index.html")
        found = {}
        for page in range(1, self.max_pages + 1):
            request = self.requests.json("", {"query": query, "region": "", "page": str(page), "vyp3CaptchaToken": ""})
            token = quote(require_token(request), safe="")
            response = self.requests.poll(lambda: self.requests.json("search-result/" + token),
                            lambda d: isinstance(d, dict) and "rows" in d,
                            wait_timeout=self.wait_timeout)
            rows = response["rows"]
            if not isinstance(rows, list):
                raise SourceError("invalid_response", "Некорректный список результатов ЕГРЮЛ")
            for row in rows:
                if isinstance(row, dict) and matches(row, query):
                    found[require_token(row, "o")] = row
            count = int(rows[0].get("cnt", 0)) if rows else 0
            if page * 20 >= count:
                return list(found.values())
        raise SourceError("pagination_limit", "Превышено ограничение страниц поиска ЕГРЮЛ")

    def download(self, row: dict) -> bytes:
        self.requests.identifier = row.get("i") or row["o"]
        token = quote(require_token(row), safe="")
        self.requests.json("vyp-request/" + token + "?r=")

        def ready(data):
            if isinstance(data, dict) and data.get("status") == "error":
                raise SourceError("expired_extract", "Истёк срок ссылки на выписку ФНС")
            return isinstance(data, dict) and data.get("status") == "ready"

        self.requests.poll(lambda: self.requests.json("vyp-status/" + token), ready, wait_timeout=self.wait_timeout)
        pdf = self.session.fetch("vyp-download/" + token)
        if not pdf.startswith(b"%PDF-"):
            raise SourceError("invalid_pdf", "ФНС вернула ответ, не являющийся PDF")
        return pdf


class TransparentBusinessClient:
    def __init__(self, session=None, *, timeout=20.0, wait_timeout=60.0, max_pages=20, captcha_handler=None):
        self.session = session or Session(PB_URL, timeout=timeout)
        self.requests = FnsRequests(self.session, "transparent_business", captcha_handler)
        self.wait_timeout = wait_timeout
        self.max_pages = max_pages

    def search(self, query: str) -> tuple[list[dict], list[dict]]:
        self.requests.identifier = query
        self.session.fetch("search.html")
        individual = len(query) in (12, 15)
        mode, field, group = ("search-ip", "queryIp", "ip") if individual else ("search-ul", "queryUl", "ul")
        found = {}
        responses = []
        for page in range(1, self.max_pages + 1):
            request = self.requests.json("search-proc.json", {
                "mode": mode, field: query, "page": str(page), "pageSize": "10", "pbCaptchaToken": "",
            })
            request_id = require_token(request, "id")
            response = self.requests.poll(lambda: self.requests.json("search-proc.json", {"id": request_id, "method": "get-response"}),
                            lambda d: d is not None, wait_timeout=self.wait_timeout)
            if not isinstance(response, dict) or not isinstance(response.get(group), dict):
                raise SourceError("invalid_response", "Изменилась структура поиска «Прозрачного бизнеса»")
            responses.append(response)
            section = response[group]
            rows = section.get("data")
            if not isinstance(rows, list):
                raise SourceError("invalid_response", "Отсутствуют строки результатов «Прозрачного бизнеса»")
            for row in rows:
                if isinstance(row, dict) and matches(row, query, pb=True):
                    found[require_token(row, "ogrn")] = row
            total = int(section.get("rowCount", len(rows)))
            page_size = int(section.get("pageSize", 10))
            if not section.get("hasMore") and page * page_size >= total:
                return list(found.values()), responses
        raise SourceError("pagination_limit", "Превышено ограничение страниц поиска «Прозрачного бизнеса»")

    def card(self, row: dict) -> dict:
        self.requests.identifier = row.get("inn") or row["ogrn"]
        token = require_token(row, "token")
        self.session.fetch("company.html?token=" + quote(token, safe=""))
        request = self.requests.json("company-proc.json", {"token": token, "method": "get-request"})
        request_id = require_token(request, "id")
        request_token = require_token(request, "token")
        response = self.requests.poll(lambda: self.requests.json("company-proc.json", {
            "token": request_token, "id": request_id, "method": "get-response",
        }), lambda d: d is not None, wait_timeout=self.wait_timeout)
        if not isinstance(response, dict) or not isinstance(response.get("vyp"), dict):
            raise SourceError("invalid_response", "В карточке ФНС отсутствует раздел vyp")
        info = response["vyp"]
        returned = info.get("ОГРН") or info.get("ОГРНИП")
        if str(returned) != str(row["ogrn"]):
            raise SourceError("identity_mismatch", "ОГРН в карточке ФНС не совпадает с результатом поиска")
        return response
