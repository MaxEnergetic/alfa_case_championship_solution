"""Small business card projected from a full parser result, without network I/O."""

from datetime import datetime
from decimal import Decimal, InvalidOperation


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal("".join(str(value).split()).replace(",", "."))
        if parsed.is_finite():
            return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
    except InvalidOperation:
        pass
    return None


def date(value):
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value)[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def present(values):
    return {k: v for k, v in values.items() if v is not None and v != "" and v != [] and v != {}}


def field(fields, label, section_contains=""):
    return next((f.get("value") for f in fields
                 if f.get("label", "").casefold() == label.casefold()
                 and section_contains in (f.get("section") or "").casefold()), None)


def parties(fields, section_contains, fallback, *, founders=False):
    groups = {}
    for f in fields:
        section = (f.get("section") or "").casefold()
        if section_contains in section:
            key = (section, f.get("item_number"))
            groups.setdefault(key, []).append(f)
    result = []
    for group in groups.values():
        name = (field(group, "Фамилия Имя Отчество")
                or field(group, "Полное наименование на русском языке")
                or field(group, "Полное наименование"))
        if name:
            party = {"name": name}
            if founders:
                party["share_percent"] = number(field(group, "Размер доли (в процентах)"))
            else:
                party["position"] = field(group, "Должность")
            result.append(present(party))
    # Do not merge a previous PB ownership snapshot into current PDF ownership.
    if not result:
        result = [present({"name": p["name"], "position": p.get("position") if not founders else None})
                  for p in fallback if p.get("name")]
    return result


def period_key(row):
    return (number(row.get("yearcode")) or 0, number(row.get("periodcode")) or 0)


def latest_rows(rows):
    dated = [row for row in rows if number(row.get("yearcode")) is not None]
    if not dated:
        return []
    latest = max(map(period_key, dated))
    return [row for row in dated if period_key(row) == latest]


def period(row):
    year, month = period_key(row)
    return f"{int(year):04d}-{int(month):02d}" if year and 1 <= month <= 12 else str(int(year)) if year else None


def total(rows, key):
    """Do not present a sum as complete if one component is missing."""
    values = [number(r.get(key)) if number(r.get("empty")) != 1 else None for r in rows]
    if values and all(v is not None for v in values):
        return number(sum((Decimal(str(v)) for v in values), Decimal(0)))
    return None


def financials(card, summary):
    years = {}

    def year_entry(year):
        year = number(year)
        if not isinstance(year, int):
            return None
        return years.setdefault(year, {"year": year})

    for row in card.get("form1", []):
        entry = year_entry(row.get("yearcode"))
        if entry is not None and number(row.get("empty")) != 1:
            entry.update(present({"income": number(row.get("revenue")), "expenses": number(row.get("expense"))}))
    taxes = {}
    for row in card.get("taxpay", []):
        taxes.setdefault(number(row.get("yearcode")), []).append(row)
    for year, rows in taxes.items():
        entry = year_entry(year)
        if entry is not None:
            entry.update(present({"taxes_paid": total(rows, "taxsum")}))
    for section, key, mapping in (
        ("form1", "income_expenses", {"income": "income", "expenses": "expenses"}),
        ("taxpay", "taxes_paid", {"amount": "taxes_paid"}),
    ):
        if not card.get(section):
            value = summary.get(key) or {}
            entry = year_entry(value.get("year"))
            if entry is not None:
                entry.update(present({dest: number(value.get(src)) for src, dest in mapping.items()}))
    return [years[y] for y in sorted(years, reverse=True) if len(years[y]) > 1]


def tax_regime(card):
    rows = latest_rows(card.get("taxmode", []))
    if not rows:
        return None
    row = rows[0]
    result = {"period": period(row)}
    flags = {"usn": "УСН", "ausn": "АУСН", "eshn": "ЕСХН", "psn": "ПСН",
             "npd": "НПД", "envd": "ЕНВД", "spr": "СРП"}
    if number(row.get("empty")) == 1 or not any(k in row for k in flags):
        return {**result, "status": "no_data"}
    # No active special-regime flag does not establish that OSNO applies.
    return {**result, "regimes": [label for key, label in flags.items() if number(row.get(key)) == 1]}


def tax_debt(card):
    rows = latest_rows(card.get("arrear", []))
    if not rows:
        return {"status": "no_data"}
    amount = total(rows, "totalsum")
    has_values = any(number(r.get("totalsum")) is not None and number(r.get("empty")) != 1 for r in rows)
    result = {"period": period(rows[0]),
              "status": "available" if amount is not None else "incomplete" if has_values else "no_data"}
    if amount is not None:
        result["amount"] = amount
    return result


def source_status(source):
    return present({"status": source.get("status"), "error": source.get("error")})


def compact_public_source(source):
    """Preserve unknown/blocked states; only include counts supplied by a source."""
    result = {key: source[key] for key in (
        "status", "error", "url", "checked_at", "reason", "lookup_performed",
        "query_scope", "query_inn", "years", "unit", "total_cases", "total_found", "truncated", "coverage",
    ) if key in source}
    for key in ("cases", "proceedings"):
        if key in source:
            rows = source[key]
            result["retrieved_count"] = len(rows)
            result["sample_limit"] = 3
            result[key] = [{k: v for k, v in row.items() if k != "fields"} for row in rows[:3]]
    return result


def compact_entity(entity):
    summary = entity.get("summary") or {}
    egrul = entity.get("egrul") or {}
    pb = entity.get("transparent_business") or {}
    extract = egrul.get("extract") or {}
    fields = extract.get("fields", [])
    card = pb.get("data") or {}
    info = card.get("vyp") or {}
    provenance = summary.get("provenance") or {}
    publications = provenance.get("pb_publication_dates") or {}
    terminated = summary.get("terminated_on") or field(fields, "Дата прекращения деятельности")
    status = summary.get("registry_status_text")
    if not status:
        status = "Деятельность прекращена" if terminated else summary.get("status_from_pb")
    main_okved = field(fields, "Код и наименование вида деятельности", "основном виде деятельности")
    capital = field(fields, "Размер (в рублях)", "уставном капитале")
    if capital is None:
        capital = (summary.get("capital") or {}).get("amount")
    staff = latest_rows(card.get("sschr", []))
    employees = ({"year": number(staff[0].get("yearcode")),
                  "count": number(staff[0].get("sschr")) if number(staff[0].get("empty")) != 1 else None}
                 if staff else summary.get("employees"))
    employees = present(employees or {})
    # A bare count without an accounting year cannot be safely presented.
    if "year" not in employees:
        employees = None
    result = present({
        "inn": entity.get("inn"),
        "ogrn": entity.get("ogrn"),
        "entity_type": entity.get("entity_type"),
        "name": summary.get("short_name") or summary.get("name"),
        "kpp": summary.get("kpp"),
        "status": status,
        "registered_on": date(summary.get("registered_on") or summary.get("ogrn_assigned_on")),
        "terminated_on": date(terminated),
        "registration_invalid_on": date(summary.get("registration_invalid_on")),
        "address": summary.get("address"),
        "main_okved": main_okved.split()[0] if main_okved else (summary.get("main_okved") or {}).get("code"),
        "capital_rub": number(capital),
        "managers": parties(fields, "без доверенности", info.get("masruk") or []),
        "founders": parties(fields, "учредител", info.get("masuchr") or [], founders=True),
        "employees": employees,
        "financials": financials(card, summary),
        "tax_regime": tax_regime(card),
        "tax_debt": tax_debt(card),
        "data_dates": present({
            "registry": date(extract.get("formed_on")),
            "pb_registry": date(provenance.get("pb_registry_snapshot_date")),
            **{name: date(publications.get("reglamentdate_" + key)) for name, key in (
                ("financials", "nbo"), ("employees", "sschr"), ("taxes_paid", "taxpay"),
                ("tax_regime", "spec"), ("tax_debt", "arrear"))},
        }),
    })
    if not result.get("managers") and summary.get("management_text"):
        position, sep, name = summary["management_text"].partition(":")
        result["managers"] = [present({"name": name.strip() if sep else position,
                                       "position": position.strip() if sep else None})]
    issues = []
    for name, source in (("egrul", egrul), ("transparent_business", pb)):
        warnings = (source.get("extract") or {}).get("warnings")
        if source.get("status") != "ok" or warnings:
            issues.append(present({"source": name, **source_status(source), "warnings": warnings}))
    if issues:
        result["issues"] = issues
    if entity.get("public_sources"):
        result["public_sources"] = {name: compact_public_source(source)
                                    for name, source in entity["public_sources"].items()}
    return result


def compact_result(full: dict) -> dict:
    """Convert a saved full result without mutating it or making requests.

    Missing optional values are omitted, actual zeroes remain zero. Entity
    registrations and source failures are preserved. All money is in rubles.
    """
    if not isinstance(full, dict) or full.get("schema_version") != "1.0" or not isinstance(full.get("entities"), list):
        raise ValueError("Ожидается полный JSON парсера со schema_version=1.0 и entities[]")
    return {
        "schema_version": "compact-1.0",
        "query": full.get("query"),
        "status": full.get("status"),
        "checked_at": full.get("finished_at"),
        "sources": {name: source_status(source) for name, source in full.get("sources", {}).items()},
        "entities": [compact_entity(entity) for entity in full["entities"]],
    }
