"""Public library API and aggregate result with per-source status."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import time
import hashlib
import math
from pathlib import Path
import uuid

from .extract import first_field, parse_pdf
from .compact import compact_result
from .identifiers import validate_identifier
from .providers import EGRUL_URL, PB_URL, EgrulClient, TransparentBusinessClient
from .transport import SourceError
from .source_cache import previous_entity, recent_entry


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def failure(exc: SourceError) -> dict:
    return {"status": "error", "error": exc.as_dict(), "fetched_at": now()}


def normalize(entity: dict) -> dict:
    """Convenient fields; the original full source responses remain authoritative."""
    egrul = entity.get("egrul", {})
    pb = entity.get("transparent_business", {})
    row = egrul.get("search_result", {})
    card = pb.get("data", {})
    info = card.get("vyp", {})
    extract = egrul.get("extract")
    pb_row = pb.get("search_result", {})
    individual = entity["entity_type"] == "individual"
    # Search and extract are live registry data; PB can be an older snapshot.
    full_name = (first_field(extract, "Полное наименование на русском языке")
                 or first_field(extract, "Фамилия Имя Отчество") or row.get("n"))
    if not full_name:
        full_name = (" ".join(info.get(k, "") for k in ("Фамилия", "Имя", "Отчество")).strip()
                     if individual else info.get("НаимЮЛПолн")) or pb_row.get("namep") or pb_row.get("namec")
    result = {
        "name": full_name,
        "short_name": first_field(extract, "Сокращенное наименование на русском языке") or row.get("c") or info.get("НаимЮЛСокр"),
        "kpp": row.get("p") or info.get("КПП"),
        "ogrn_assigned_on": row.get("r") or info.get("ДатаОГРН") or info.get("ДатаОГРНИП"),
        "registered_on": first_field(extract, "Дата регистрации") or info.get("ДатаРег"),
        "registry_status_text": first_field(extract, "Состояние") or first_field(extract, "Способ прекращения"),
        "terminated_on": row.get("e"),
        "registration_invalid_on": row.get("v"),
        "address": first_field(extract, "Адрес юридического лица") or info.get("АдресРФ") or info.get("Адрес"),
        "region": row.get("rn") or info.get("НаимРегион"),
        "management_text": row.get("g"),
        "status_from_pb": info.get("sulst_name_ex") or info.get("НаимСтатусЮЛ") or info.get("НаимСтатусИП"),
        "liquidated_from_pb": card.get("liquidated") if not individual else None,
        "main_okved": {"code": info.get("КодОКВЭД"), "name": info.get("НаимОКВЭД"), "type": info.get("ТипОКВЭД")},
        "msp": {"category_code": info.get("rsmpcategory"), "included_on": info.get("rsmpdate")},
        "capital": {"type": info.get("НаимВидКап"), "amount": info.get("СумКап")},
        "employees": {"count": info.get("sschr"), "year": info.get("sschr_yearcode")},
        # PB calls these revenue/expense, but form1 is aggregate income/expenses,
        # not a complete profit-and-loss statement from GIR BO.
        "income_expenses": {"income": info.get("revenuesum"), "expenses": info.get("expensesum"), "year": info.get("form1_yearcode")},
        "taxes_paid": {"amount": info.get("taxpaysum"), "year": info.get("taxpay_yearcode")},
        "provenance": {
            "registry_extract_formed_on": extract.get("formed_on") if extract else None,
            "pb_registry_snapshot_date": info.get("ДатаВып"),
            "pb_publication_dates": card.get("oddate"),
        },
    }
    return result


def _registry_source(name, query, client, destination):
    result = {"sources": {}}
    entities = {}

    def get_entity(ogrn, inn):
        # Values returned by a remote service must not become unchecked paths.
        ogrn, ogrn_kind = validate_identifier(str(ogrn))
        if ogrn_kind not in ("ogrn", "ogrnip"):
            raise SourceError("invalid_response", "ФНС вернула некорректный ОГРН")
        if ogrn not in entities:
            entities[ogrn] = {"inn": str(inn) if inn else None, "ogrn": ogrn,
                              "entity_type": "individual" if len(ogrn) == 15 else "legal",
                              "egrul": {"status": "not_found"},
                              "transparent_business": {"status": "not_found"}}
        return entities[ogrn]

    if name == "egrul":
        try:
            rows = client.search(query)
            result["sources"]["egrul"] = {"url": EGRUL_URL, "status": "ok" if rows else "not_found", "matches": len(rows)}
            stop_error = None
            for row in rows:
                try:
                    entity = get_entity(row["o"], row.get("i"))
                except (ValueError, KeyError) as exc:
                    raise SourceError("invalid_response", "Некорректные идентификаторы в ответе ЕГРЮЛ") from exc
                entry = {"status": "ok", "search_result": row, "fetched_at": now()}
                entity["egrul"] = entry
                try:
                    if stop_error:
                        raise stop_error
                    content = client.download(row)
                    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
                    path = destination / f"{'egrip' if len(entity['ogrn']) == 15 else 'egrul'}_{entity['ogrn']}_{suffix}.pdf"
                    path.write_bytes(content)
                    entry["pdf"] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                    extract = parse_pdf(content)
                    pdf_ogrn = first_field(extract, "ОГРН") or first_field(extract, "ОГРНИП")
                    if pdf_ogrn and pdf_ogrn.replace(" ", "") != entity["ogrn"]:
                        raise SourceError("identity_mismatch", "ОГРН в PDF не совпадает с результатом поиска")
                    entry["extract"] = extract
                    if entry["extract"]["warnings"]:
                        entry["status"] = "partial"
                except SourceError as exc:
                    entry.update(failure(exc))
                    entry["status"] = "partial"  # search result / downloaded PDF still available
                    if exc.code in ("captcha_required", "rate_limited"):
                        stop_error = exc
                except OSError as exc:
                    entry.update(failure(SourceError("file_error", f"Не удалось сохранить PDF: {exc}")))
                    entry["status"] = "partial"
        except SourceError as exc:
            result["sources"]["egrul"] = {"url": EGRUL_URL, **failure(exc)}
    else:
        try:
            rows, search_responses = client.search(query)
            result["sources"]["transparent_business"] = {
                "url": PB_URL, "status": "ok" if rows else "not_found", "matches": len(rows),
                "search_responses": search_responses,
            }
            stop_error = None
            for row in rows:
                try:
                    entity = get_entity(row["ogrn"], row.get("inn"))
                except (ValueError, KeyError) as exc:
                    raise SourceError("invalid_response", "Некорректные идентификаторы в ответе «Прозрачного бизнеса»") from exc
                entry = {"status": "ok", "search_result": row, "fetched_at": now()}
                entity["transparent_business"] = entry
                try:
                    if stop_error:
                        raise stop_error
                    entry["data"] = client.card(row)
                    entry["available_sections"] = list(entry["data"])
                except SourceError as exc:
                    entry.update(failure(exc))
                    entry["status"] = "partial"
                    if exc.code in ("captcha_required", "rate_limited"):
                        stop_error = exc
        except SourceError as exc:
            result["sources"]["transparent_business"] = {"url": PB_URL, **failure(exc)}
    return result["sources"][name], entities


def _cached_registry(previous, query, name, destination):
    entity = previous_entity(previous, query)
    if entity is None or previous.get("sources", {}).get(name, {}).get("status") != "ok":
        return None
    entry = recent_entry(entity.get(name), timestamp="fetched_at")
    if entry is None or entry["status"] != "ok" or not entry.get("search_result"):
        return None
    if name == "egrul":
        pdf = entry.get("pdf", {})
        path = Path(pdf.get("path", "")).resolve()
        if not path.is_relative_to(destination) or not path.is_file() or not entry.get("extract"):
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != pdf.get("sha256"):
            return None
    elif not isinstance(entry.get("data", {}).get("vyp"), dict):
        return None
    source = deepcopy(previous["sources"][name])
    source["cache_reused"] = True
    return source, {query: {"inn": entity.get("inn"), "ogrn": query,
                           "entity_type": entity["entity_type"], name: entry}}


def parse_company(identifier: str, *, output_dir: str | Path = "data", timeout: float = 20.0,
                  wait_timeout: float = 60.0, full: bool = False, enrich: bool = True,
                  max_pages: int = 3, egrul_client=None, pb_client=None, public_clients=None,
                  captcha_handler=None, parallel_sources=False, previous_result=None) -> dict:
    """Return JSON-serializable public data, and save original PDF(s).

    One INN can correspond to several historic IP registrations. None are
    silently discarded. A failed source does not erase the other one's data.
    Validation errors raise ValueError; upstream failures appear in the JSON.
    The default is a compact business card. Pass full=True for all raw data.
    """
    query, kind = validate_identifier(identifier)
    if any(not math.isfinite(x) or x <= 0 for x in (timeout, wait_timeout)):
        raise ValueError("Таймауты должны быть положительными конечными числами")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages должен быть целым числом от 1 до 10")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    egrul_client = egrul_client or EgrulClient(timeout=timeout, wait_timeout=wait_timeout, captcha_handler=captcha_handler)
    pb_client = pb_client or TransparentBusinessClient(timeout=timeout, wait_timeout=wait_timeout, captcha_handler=captcha_handler)
    result = {"schema_version": "1.0", "query": query, "query_type": kind, "started_at": now(),
              "status": None, "sources": {}, "entities": []}
    entities = {}

    def get_entity(ogrn, inn):
        # Values returned by a remote service must not become unchecked paths.
        ogrn, ogrn_kind = validate_identifier(str(ogrn))
        if ogrn_kind not in ("ogrn", "ogrnip"):
            raise SourceError("invalid_response", "ФНС вернула некорректный ОГРН")
        if ogrn not in entities:
            entities[ogrn] = {"inn": str(inn) if inn else None, "ogrn": ogrn,
                              "entity_type": "individual" if len(ogrn) == 15 else "legal",
                              "egrul": {"status": "not_found"},
                              "transparent_business": {"status": "not_found"}}
        return entities[ogrn]

    def fetch_source(name, client):
        started = time.monotonic()
        cached = _cached_registry(previous_result, query, name, destination)
        source, entries = cached or _registry_source(name, query, client, destination)
        source["duration_seconds"] = round(time.monotonic() - started, 3)
        return source, entries

    clients = {"egrul": egrul_client, "transparent_business": pb_client}
    if parallel_sources:
        # Separate hosts and clients, one company at a time; never share a
        # cookie session or mutable entity dictionary between workers.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {name: pool.submit(fetch_source, name, client) for name, client in clients.items()}
            collected = {name: future.result() for name, future in futures.items()}
    else:
        collected = {name: fetch_source(name, client) for name, client in clients.items()}
    for name, (source, entries) in collected.items():
        result["sources"][name] = source
        for row in entries.values():
            entity = get_entity(row["ogrn"], row.get("inn"))
            if entity.get("inn") and row.get("inn") and entity["inn"] != row["inn"]:
                raise SourceError("identity_mismatch", "Источники вернули разные ИНН для одного ОГРН")
            if not entity.get("inn"):
                entity["inn"] = row.get("inn")
            entity[name] = row[name]

    for entity in entities.values():
        for name in ("egrul", "transparent_business"):
            source = result["sources"][name]
            if entity[name]["status"] == "not_found" and source["status"] == "error":
                entity[name] = {"status": "error", "error": source["error"]}
        entity["summary"] = normalize(entity)
    result["entities"] = sorted(entities.values(), key=lambda e: e["ogrn"])
    for name, source in result["sources"].items():
        if source["status"] == "ok" and any(e[name]["status"] != "ok" for e in entities.values()):
            source["status"] = "partial"
    statuses = [s["status"] for s in result["sources"].values()]
    if not entities:
        result["status"] = "not_found" if all(s == "not_found" for s in statuses) else "error"
    else:
        result["status"] = "ok" if all(s == "ok" for s in statuses) else "partial"
    result["finished_at"] = now()
    if enrich:
        from .public_sources import enrich_result
        enrich_result(result, timeout=timeout, max_pages=max_pages, clients=public_clients,
                      captcha_handler=captcha_handler, previous_result=previous_result)
    return result if full else compact_result(result)
