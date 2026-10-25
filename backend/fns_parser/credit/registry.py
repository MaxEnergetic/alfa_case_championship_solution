"""Import server-side parser results; only the chosen registration can override answers."""

from copy import deepcopy
from datetime import date

from ..compact import compact_result
from .dialogue import replay
from .questions import CreditError

ALLOWED_SOURCES = {"egrul", "transparent_business", "girbo"}


def retained_sources(result):
    result = compact_result(result) if result.get("schema_version") == "1.0" else deepcopy(result)
    result["sources"] = {k: v for k, v in result.get("sources", {}).items() if k in ALLOWED_SOURCES}
    for entity in result.get("entities", []):
        entity["public_sources"] = {k: v for k, v in entity.get("public_sources", {}).items() if k == "girbo"}
        if "issues" in entity:
            entity["issues"] = [i for i in entity["issues"] if i.get("source") in ALLOWED_SOURCES]
    # Old saved jobs may be partial only because of the five removed sources.
    if result.get("entities"):
        bad = any(s.get("status") not in {"ok", "not_found", "not_applicable"} for s in result["sources"].values())
        bad = bad or any(e.get("issues") or any(s.get("status") not in {"ok", "not_found", "not_applicable"}
                        for s in e.get("public_sources", {}).values()) for e in result["entities"])
        result["status"] = "partial" if bad else "ok"
    return result


def attach(record, job, selected_ogrn):
    if job["state"] != "completed" or not job.get("result"):
        raise CreditError(409, "registry_not_ready", "Дождитесь завершения задания парсера")
    if job['result'].get('schema_version') not in ('1.0', 'compact-1.0'):
        raise CreditError(422, 'not_registry_job', 'Нужен результат задания парсера, а не скоринга')
    answers = replay(record["entries"], date.fromisoformat(record["date"])).answers
    inn = answers.get("inn")
    if not inn:
        raise CreditError(409, "inn_required", "Сначала укажите ИНН в анкете")
    result = retained_sources(job["result"])
    entities = result.get("entities", [])
    if any(e.get("inn") != inn for e in entities):
        raise CreditError(409, "company_mismatch", "Задание относится к другому ИНН")
    if not entities and job["identifier"] != inn:
        raise CreditError(409, "company_mismatch", "Не удалось подтвердить связь ОГРН задания с ИНН анкеты")
    if selected_ogrn and not any(e.get("ogrn") == selected_ogrn for e in entities):
        raise CreditError(422, "invalid_ogrn_selection", "ОГРН отсутствует в результате задания")
    if not selected_ogrn:
        if len(entities) == 1:
            selected_ogrn = entities[0]["ogrn"]
        elif any(e.get("ogrn") == job["identifier"] for e in entities):
            selected_ogrn = job["identifier"]
    # Several registrations under one INN remain unselected until an explicit choice.
    record["registry"], record["selected_ogrn"] = result, selected_ogrn
    record['scoring'], record['scoring_job_id'] = None, None


def selected_entity(record):
    return next((e for e in (record.get("registry") or {}).get("entities", [])
                 if e.get("ogrn") == record.get("selected_ogrn")), None)


def authoritative_answers(record):
    """Only semantically identical, nonempty registry facts override self-reports.

    Annual accounting income/revenue is NOT a monthly revenue answer; balance-sheet
    borrowings are NOT monthly payments or arrears. Those remain separate dated facts.
    """
    entity = selected_entity(record)
    if not entity:
        return {}
    issues = {i.get("source"): i.get("status") for i in entity.get("issues", [])}
    sources = (record.get("registry") or {}).get("sources", {})
    if not any(issues.get(s, sources.get(s, {}).get("status")) == "ok" for s in ("egrul", "transparent_business")):
        return {}
    facts = {}
    if entity.get("inn"):
        facts["inn"] = entity["inn"]
    if entity.get("entity_type") == "individual":
        facts["form"] = "ip"
    elif entity.get("entity_type") == "legal":
        name = (entity.get("name") or "").strip().upper()
        if name.startswith(('ООО ', 'ООО«', 'ООО"', 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ')):
            facts["form"] = "ooo"
        elif name.startswith(('АО ', 'ПАО ', 'ЗАО ', 'ОАО ', 'ГУП ', 'МУП ', 'ФГУП ', 'АНО ', 'НКО ')):
            facts["form"] = "other_legal"
    if entity.get("terminated_on") or entity.get("registration_invalid_on"):
        facts["registered"] = "no"
    elif "действующ" in (entity.get("status") or "").lower():
        facts["registered"] = "yes"
    return facts
