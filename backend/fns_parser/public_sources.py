"""Free public web sources. No subscriber APIs, accounts or captcha solvers.

Endpoints follow the public websites' own search forms (checked 2026-10-23).
Their contracts are unofficial; unexpected responses fail closed as unknown.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import re
from urllib.parse import urlencode, urljoin

from .compact import number, present
from .html_tools import Tree, html_text
from .transport import Session, SourceError, check_html_challenge
from .source_cache import previous_entity, recent_entry

logger = logging.getLogger(__name__)
URLS = {
    "girbo": "https://bo.nalog.gov.ru/",
    "arbitration": "https://kad.arbitr.ru/",
    "fssp": "https://fssp.gov.ru/iss/ip",
    "fedresurs": "https://fedresurs.ru/",
    "bankruptcy": "https://bankrot.fedresurs.ru/",
    "pledges": "https://www.reestr-zalogov.ru/search/index",
}
DONE = {"ok", "not_found", "not_applicable"}


def now():
    return datetime.now(timezone.utc).isoformat()


def strict_json(raw):
    def invalid(value):
        raise ValueError(value)
    try:
        return json.loads(raw, parse_constant=invalid)
    except (ValueError, UnicodeError) as exc:
        raise SourceError("invalid_response", "Источник вернул неожиданный формат вместо JSON") from exc


class PublicClient:
    name = ""

    def __init__(self, *, timeout=20, max_pages=3, session=None):
        self.session = session or Session(URLS[self.name], timeout=timeout, min_interval=0.7, retries=0)
        self.max_pages = max_pages

    def get_json(self, path):
        return strict_json(self.session.fetch(path, max_bytes=8 * 1024 * 1024))


# GIR BO's ordinary non-bank BFO forms store monetary lines in thousands of RUB.
# The public UI defaults to startRate=1 (thousands), not the subscriber API's units.
BALANCE = {"assets_rub": "1600", "equity_rub": "1300", "cash_rub": "1250",
           "receivables_rub": "1230", "long_term_borrowings_rub": "1410",
           "short_term_borrowings_rub": "1510", "accounts_payable_rub": "1520",
           "long_term_liabilities_rub": "1400", "short_term_liabilities_rub": "1500"}
PROFIT = {"revenue_rub": "2110", "net_profit_rub": "2400", "interest_payable_rub": "2330"}


def rubles(value):
    parsed = number(value)
    return number(Decimal(str(parsed)) * 1000) if parsed is not None else None


def bfo_summary(reports, inn):
    years = []
    for report in reports:
        info = report.get("organizationInfo") or {}
        if info.get("inn") and info["inn"] != inn:
            raise SourceError("identity_mismatch", "ИНН бухгалтерской отчётности не совпадает с запросом")
        if report.get("isCb"):
            continue  # Bank forms use a different taxonomy; preserve them only in raw.
        for item in report.get("typeCorrections", []):
            if item.get("type") != 12:
                continue  # Do not label an interim statement as an annual one.
            correction = item.get("correction") or {}
            ci = correction.get("bfoOrganizationInfo") or {}
            if ci.get("inn") and ci["inn"] != inn:
                raise SourceError("identity_mismatch", "ИНН корректировки отчётности не совпадает с запросом")
            year = number(report.get("period"))
            if not isinstance(year, int):
                raise SourceError("invalid_response", "В ГИР БО отсутствует отчётный год")
            entry = present({"year": year, "correction": correction.get("correctionVersion"),
                             "submitted_on": correction.get("datePresent"),
                             "published_on": report.get("publishedCorrectionDate")})
            for section, mapping in (("balance", BALANCE), ("financialResult", PROFIT)):
                values = correction.get(section) or {}
                entry.update(present({dest: rubles(values.get("current" + code)) for dest, code in mapping.items()}))
            if all(k in entry for k in ("long_term_borrowings_rub", "short_term_borrowings_rub")):
                entry["borrowings_rub"] = number(sum(Decimal(str(entry[k])) for k in (
                    "long_term_borrowings_rub", "short_term_borrowings_rub")))
            years.append(entry)
    return sorted(years, key=lambda y: y["year"], reverse=True)


class GirboClient(PublicClient):
    name = "girbo"

    def lookup(self, entity):
        if entity["entity_type"] != "legal":
            return {"status": "not_applicable", "reason": "ГИР БО содержит бухгалтерскую отчётность организаций, не ИП"}
        inn, ogrn = entity.get("inn"), entity["ogrn"]
        if not inn:
            raise SourceError("missing_inn", "Для поиска ГИР БО необходим ИНН из ЕГРЮЛ")
        raw_search = []
        match = None
        for page in range(self.max_pages):
            data = self.get_json("/advanced-search/organizations/search?" + urlencode({"query": inn, "page": page}))
            if not isinstance(data, dict) or not isinstance(data.get("content"), list):
                raise SourceError("invalid_response", "Неизвестный формат поиска ГИР БО")
            raw_search.append(data)
            matches = [row for row in data["content"] if html_text(str(row.get("inn", ""))) == inn
                       and str(row.get("ogrn", "")) == ogrn]
            if len(matches) > 1:
                raise SourceError("ambiguous_match", "ГИР БО вернул несколько карточек с одинаковыми ИНН и ОГРН")
            if matches:
                match = matches[0]
                break
            if data.get("last") is True or data.get("totalPages") == 0:
                return {"status": "not_found", "raw": {"search": raw_search}}
        if match is None:
            raise SourceError("search_limit", "Достигнут лимит страниц поиска ГИР БО; совпадение не установлено")
        org_id = match.get("id")
        if not isinstance(org_id, int) or isinstance(org_id, bool) or org_id <= 0:
            raise SourceError("invalid_response", "Некорректный ID карточки ГИР БО")
        info = self.get_json(f"/nbo/organizations/{org_id}")
        if not isinstance(info, dict) or info.get("inn") != inn or info.get("ogrn") != ogrn:
            raise SourceError("identity_mismatch", "Идентификаторы карточки ГИР БО не совпадают с запросом")
        result = {"status": "ok", "url": f"https://bo.nalog.gov.ru/organizations-card/{org_id}",
                  "raw_unit": "thousand_rub", "unit": "rub",
                  "raw": {"search": raw_search, "organization": info}}
        try:
            reports = self.get_json(f"/nbo/organizations/{org_id}/bfo/")
        except SourceError as exc:
            return {**result, "status": "partial", "error": exc.as_dict()}
        if not isinstance(reports, list):
            raise SourceError("invalid_response", "ГИР БО не вернул список отчётности")
        years = bfo_summary(reports, inn)
        result["years"] = years
        result["raw"]["reports"] = reports
        if not years:
            result["status"] = "partial"
            result["error"] = {"code": "no_supported_reports", "message": "Нет доступной годовой отчётности обычного формата; см. raw"}
        return result


def parse_arbitration(html):
    check_html_challenge(html.encode())
    tree = Tree(html).root
    counts = list(tree.find("input", id="documentsTotalCount"))
    if not counts or not re.fullmatch(r"\d+", counts[0].attrs.get("value") or ""):
        raise SourceError("invalid_response", "КАД не вернул счётчик результатов; отсутствие дел не установлено")
    total = int(counts[0].attrs["value"])
    cases = []
    for row in tree.find("tr"):
        link = next((a for a in row.find("a") if re.fullmatch(
            r"(?:https://kad\.arbitr\.ru)?/Card/[0-9a-fA-F-]{36}", a.attrs.get("href") or "", re.I)), None)
        if link is None:
            continue
        cells = {cls: next(row.find("td", cls=cls), None) for cls in ("num", "court", "plaintiff", "respondent")}
        item = {"number": link.text(), "url": urljoin(URLS["arbitration"], link.attrs["href"])}
        for cls, dest in (("court", "court"), ("plaintiff", "plaintiffs"), ("respondent", "defendants")):
            if cells[cls] is not None:
                item[dest] = cells[cls].text()
        if cells["num"] is not None:
            text = cells["num"].text()
            match = re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", text)
            if match:
                item["registered_on"] = datetime.strptime(match[0], "%d.%m.%Y").date().isoformat()
        cases.append(item)
    if total and not cases:
        raise SourceError("invalid_response", "КАД сообщил о делах, но структура списка не распознана")
    return total, cases


class ArbitrationClient(PublicClient):
    name = "arbitration"

    def lookup(self, entity):
        inn = entity.get("inn")
        if not inn:
            raise SourceError("missing_inn", "Для поиска КАД необходим ИНН")
        # Follow the site's public 'full version' link; cookies stay in this session.
        self.session.fetch("/Version/Change?mode=Full&returnUrl=%2F", max_bytes=2 * 1024 * 1024)
        cases, raw, seen = [], [], set()
        result = {"status": "ok", "query_scope": "inn", "query_inn": inn, "cases": cases, "raw": raw}
        for page in range(1, self.max_pages + 1):
            try:
                html = self.session.fetch("/Kad/SearchInstances", json_body={
                    "Page": page, "Count": 25, "Courts": [], "DateFrom": None, "DateTo": None,
                    "Sides": [{"Name": inn, "Type": -1, "ExactMatch": True}],
                    "Judges": [], "CaseNumbers": [], "WithVKSInstances": False,
                }, headers={"Referer": URLS[self.name], "x-date-format": "iso", "X-Requested-With": "XMLHttpRequest"},
                    max_bytes=4 * 1024 * 1024).decode("utf-8-sig")
                total, page_cases = parse_arbitration(html)
                raw.append(html)
                result["total_cases"] = total
                if cases and not any(c["url"] not in seen for c in page_cases):
                    raise SourceError("pagination_stalled", "КАД повторил уже полученные дела")
                for case in page_cases:
                    if case["url"] not in seen:
                        cases.append(case)
                        seen.add(case["url"])
                if len(cases) >= total:
                    break
            except SourceError as exc:
                if not cases:
                    raise
                result.update(status="partial", error=exc.as_dict())
                break
        result["truncated"] = len(cases) < result.get("total_cases", 0)
        if result["truncated"] and result["status"] == "ok":
            result.update(status="partial", error={"code": "page_limit", "message": "Получена только часть дел: достигнут лимит страниц"})
        return result


def fssp_payload(raw):
    text = raw.decode("utf-8-sig")
    # Strict JSONP unwrapping, never eval/exec of the server's JavaScript.
    if not text.lstrip().startswith("{"):
        match = re.fullmatch(r"\s*(?:/\*\*/\s*typeof fnsCallback === 'function' &&\s*)?fnsCallback\((.*)\);?\s*", text, re.S)
        if not match:
            raise SourceError("invalid_response", "Неизвестный ответ поиска ФССП")
        text = match[1]
    payload = strict_json(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise SourceError("invalid_response", "ФССП не вернула HTML результатов")
    return payload


def parse_fssp(raw):
    payload = fssp_payload(raw)
    html = payload["data"]
    check_html_challenge(html.encode())
    if payload.get("err") or payload.get("e"):
        raise SourceError("upstream_error", "ФССП сообщила об ошибке поиска")
    tree = Tree(html).root
    plain = tree.text().casefold()
    if any(phrase in plain for phrase in ("по вашему запросу ничего не найдено", "по вашему запросу ничего не найдено.", "по вашему запросу не найдено")):
        return {"status": "ok", "proceedings": [], "total_found": 0, "truncated": False, "raw": payload}
    table = next((t for t in tree.find("table") if "Исполнительное производство" in t.text()), None)
    if table is None:
        raise SourceError("invalid_response", "Таблица ФССП не распознана; отсутствие производств не установлено")
    columns, proceedings = [], []
    for row in table.find("tr"):
        headers = list(row.find("th"))
        if headers:
            columns = [h.text() for h in headers]
            continue
        cells = [c.text() for c in row.children if hasattr(c, "tag") and c.tag == "td"]
        if not cells:
            continue
        if not columns or len(cells) != len(columns):
            raise SourceError("invalid_response", "Изменились столбцы таблицы ФССП")
        item = {"fields": dict(zip(columns, cells))}
        for label, value in item["fields"].items():
            if label.startswith("Исполнительное производство"):
                match = re.search(r"\d+/\d{2}/\d{5}-ИП", value)
                if match:
                    item["number"] = match[0]
            if "предмет исполнения" in label.casefold():
                item["subject"] = value
                for phrase, key in (("Сумма долга", "debt_rub"), ("Остаток долга", "remaining_debt_rub")):
                    match = re.search(phrase + r"\s*:?\s*([\d \xa0]+[.,]\d{2}|\d+)\s*(?:руб|р\.)", value, re.I)
                    if match:
                        item[key] = number(match[1])
        if "number" not in item:
            raise SourceError("invalid_response", "Не распознан номер производства ФССП")
        proceedings.append(item)
    if not proceedings:
        raise SourceError("invalid_response", "Пустая нераспознанная таблица ФССП")
    # No verified pagination contract: never present one table as an exhaustive list.
    return {"status": "partial", "proceedings": proceedings, "retrieved_count": len(proceedings),
            "coverage": "first_page", "truncated": True, "raw": payload,
            "error": {"code": "coverage_unknown", "message": "Получена таблица ФССП; полнота и последующие страницы не подтверждены"}}


class FsspClient(PublicClient):
    name = "fssp"

    def __init__(self, *, captcha_handler=None, **kwargs):
        super().__init__(**kwargs)
        self.captcha_handler = captcha_handler
        if kwargs.get("session") is None:
            self.session = Session("https://is-node5.fssp.gov.ru/", timeout=kwargs.get("timeout", 20),
                                   min_interval=0.7, retries=0)

    def lookup(self, entity):
        if entity["entity_type"] != "legal":
            return {"status": "not_applicable", "reason": "ФССП не поддерживает этот поиск по ИНН ИП; нужны ФИО, дата рождения и регион"}
        inn = entity.get("inn")
        if not inn:
            raise SourceError("missing_inn", "Для поиска ФССП необходим ИНН юридического лица")
        # Host v5 is declared in the public form at fssp.gov.ru/iss/ip.
        params = {"system": "ip", "is[variant]": 5, "is[inn]": inn,
                  "is[extended]": 1, "nocache": 1, "callback": "fnsCallback"}
        def fetch(values):
            return self.session.fetch("/ajax_search?" + urlencode(values), max_bytes=4 * 1024 * 1024,
                                      headers={"Referer": URLS[self.name]})

        raw = fetch(params)
        if self.captcha_handler is not None:
            from .manual_captcha import fssp_challenge
            for attempt in range(1, 6):
                challenge = fssp_challenge(fssp_payload(raw)["data"], inn)
                if challenge is None:
                    break
                action, code = self.captcha_handler(source=self.name, inn=inn, image=challenge,
                                                     attempt=attempt, source_url=URLS[self.name])
                if action == "skip":
                    raise SourceError("captcha_skipped", "Ручная проверка пропущена пользователем")
                # The answer comes only from the human-facing form. The original
                # CookieJar and query are retained; no token synthesis or solving.
                raw = fetch({**params, **({"code": code} if action == "answer" else {})})
        return {"query_scope": "inn", "query_inn": inn, **parse_fssp(raw)}


class ManualRegistryClient(PublicClient):
    """Availability check only. A reachable homepage does not prove no records."""

    def __init__(self, name, **kwargs):
        self.name = name
        super().__init__(**kwargs)

    def lookup(self, entity):
        if self.name == "pledges":
            # Public search's getToken() always invokes captcha.show(). Do not
            # send a request without that required token or pretend it was a search.
            return {"status": "manual_required", "lookup_performed": False,
                    "error": {"code": "captcha_required", "message": "Поиск ФНП требует капчу на сайте; автоматический поиск не выполнен"}}
        self.session.fetch(URLS[self.name], max_bytes=2 * 1024 * 1024)
        return {"status": "manual_required", "lookup_performed": False,
                "error": {"code": "browser_required", "message": "Доступна главная страница; поиск записей Федресурса автоматически не выполнен"}}


def default_clients(timeout, max_pages, captcha_handler=None):
    kwargs = {"timeout": timeout, "max_pages": max_pages}
    # Product scope: EGRUL/EGRIP + Transparent Business + GIR BO only.
    # The old adapters stay available for offline fixtures, but never run by default.
    return {"girbo": GirboClient(**kwargs)}


def run_source(name, client, entity):
    try:
        result = client.lookup(entity)
    except SourceError as exc:
        result = {"status": "manual_required" if exc.code in ("captcha_required", "captcha_timeout", "captcha_skipped") else "error", "error": exc.as_dict()}
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        logger.warning("Unexpected %s response: %s", name, type(exc).__name__)
        result = {"status": "error", "error": {"code": "invalid_response", "message": "Изменился формат ответа источника"}}
    if isinstance(client, ManualRegistryClient):
        result["lookup_performed"] = False
    return {"url": URLS[name], "checked_at": now(), **result}


def enrich_result(result, *, timeout=20, max_pages=3, clients=None, captcha_handler=None,
                  previous_result=None):
    """Add source-specific results to a full result, in place. Isolated failures.

    Different hosts run concurrently; one client is never used concurrently.
    Repeated INNs of historic IP registrations share only INN-scoped searches.
    """
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages должен быть целым числом от 1 до 10")
    if not result.get("entities"):
        return result
    clients = default_clients(timeout, max_pages, captcha_handler=captcha_handler) if clients is None else clients
    previous = previous_entity(previous_result, result.get("query", ""))

    def for_source(name, client):
        cache, entries, stop = {}, [], None
        for entity in result["entities"]:
            key = entity.get("inn") if name in ("arbitration", "fssp") else entity["ogrn"]
            if key not in cache:
                saved = None
                if previous and previous.get("ogrn") == entity["ogrn"] and previous.get("inn") == entity.get("inn"):
                    saved = recent_entry(previous.get("public_sources", {}).get(name), timestamp="checked_at")
                if saved is not None:
                    cache[key] = saved
                elif stop is not None:
                    cache[key] = {"url": URLS[name], "checked_at": now(), "status": stop["status"],
                                  "error": stop["error"], "lookup_performed": False}
                else:
                    cache[key] = run_source(name, client, entity)
                    if cache[key].get("error", {}).get("code") in ("captcha_required", "rate_limited", "access_restricted"):
                        # A partial result belongs to this registration only.
                        # Subsequent registrations retain the block, not its data.
                        stop = {"status": "manual_required" if cache[key]["error"]["code"] == "captcha_required" else "error",
                                "error": cache[key]["error"]}
            entries.append(deepcopy(cache[key]))
        return entries

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(for_source, name, client) for name, client in clients.items()}
        for name, future in futures.items():
            entries = future.result()
            for entity, entry in zip(result["entities"], entries):
                entity.setdefault("public_sources", {})[name] = entry
            statuses = {entry["status"] for entry in entries}
            status = next(iter(statuses)) if len(statuses) == 1 else "ok" if statuses <= DONE else "partial"
            result["sources"][name] = {"url": URLS[name], "status": status}
            failures = [e for e in entries if e["status"] not in DONE]
            if failures:
                result["status"] = "partial"
                if failures[0].get("error"):
                    result["sources"][name]["error"] = failures[0]["error"]
    result["finished_at"] = now()
    return result
