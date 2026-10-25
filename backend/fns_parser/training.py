"""Project parser JSON onto the supplied 65-column scoring template.

Missingness is recorded before zero filling. Identity, metadata and loan
outcomes never enter the numeric feature matrix. No credit labels are inferred.
"""
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from .compact import compact_result, number
from .identifiers import validate_identifier


TEMPLATE_COLUMNS = (
    "meta_record_type,meta_source_file,meta_application_id,meta_application_date,"
    "meta_features_as_of,meta_inn,meta_company_name,meta_questionnaire_version,"
    "entity_type,registered_on,registry_status,main_okved,capital_rub,founders_count,"
    "largest_founder_share_pct,employees_year,employees_count,meta_financial_amount_unit,"
    "financial_year_t0,income_t0_raw,expenses_t0_raw,taxes_paid_t0_raw,"
    "financial_year_t1,income_t1_raw,expenses_t1_raw,taxes_paid_t1_raw,"
    "financial_year_t2,income_t2_raw,expenses_t2_raw,taxes_paid_t2_raw,"
    "tax_regime,tax_regime_period,tax_debt_status,tax_debt_amount_rub,tax_debt_period,"
    "meta_registry_data_date,meta_financials_data_date,meta_employees_data_date,"
    "meta_taxes_paid_data_date,meta_tax_debt_data_date,purpose,new_business_scope,"
    "requested_amount_rub,requested_term_months,cash_gap_days,funding_frequency,"
    "collateral_available,collateral_type,collateral_value_rub,own_funds_rub,project_cost_rub,"
    "meta_answers_state,monthly_operating_inflows_avg_6m_rub,"
    "monthly_cash_available_for_debt_avg_6m_rub,current_total_debt_rub,"
    "current_monthly_debt_service_rub,current_max_dpd,requested_product_code,"
    "outcome_loan_issued_date,outcome_observed_through,outcome_default_date,"
    "outcome_default_reason,meta_target_definition_version,meta_label_status,target_default_12m"
).split(",")
EXTRA_COLUMNS = ["meta_ogrn", "meta_parser_status", "meta_last_attempt_status", "meta_egrul_status",
                 "meta_transparent_business_status", "meta_girbo_status"]
CATEGORICAL = {"entity_type", "registry_status", "main_okved", "tax_regime", "tax_debt_status",
               "purpose", "new_business_scope", "funding_frequency", "collateral_available",
               "collateral_type", "requested_product_code"}
DATE_FEATURES = {"registered_on": "days_since_1970-01-01",
                 "tax_regime_period": "months_since_1970-01",
                 "tax_debt_period": "months_since_1970-01"}


def is_feature(field):
    return not field.startswith(("meta_", "outcome_", "target_"))


NUMERIC = {f for f in TEMPLATE_COLUMNS if is_feature(f)} - CATEGORICAL - DATE_FEATURES.keys()
NUMERIC.add("target_default_12m")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def read_template(path=None):
    if path is None:
        return list(TEMPLATE_COLUMNS)
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        columns = next(csv.reader(source))
    if len(columns) != len(set(columns)) or set(columns) != set(TEMPLATE_COLUMNS):
        raise ValueError("CSV-шаблон должен содержать исходные 65 колонок без дубликатов")
    return columns


def clean_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return None if value.casefold() in {"unknown", "null", "none", "nan", "n/a", "не знаю"} else value


def clean_date(value, monthly=False):
    value = clean_text(value)
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value + "-01" if monthly and len(value) == 7 else value[:10])
        return parsed.isoformat()[:7] if monthly else parsed.isoformat()
    except ValueError:
        return None


def normalize_request(value):
    """Keep leading zeroes; prefer OGRN to avoid ambiguous historic registrations."""
    if isinstance(value, str):
        value, kind = validate_identifier(value)
        return {"identifier": value, "inn": value if kind.startswith("inn_") else None,
                "ogrn": value if kind in {"ogrn", "ogrnip"} else None}
    if not isinstance(value, dict):
        raise ValueError("Ожидается строка ИНН/ОГРН или объект с inn/ogrn")
    inn, ogrn = value.get("inn") or value.get("meta_inn"), value.get("ogrn") or value.get("meta_ogrn")
    if inn is not None:
        inn, kind = validate_identifier(inn)
        if not kind.startswith("inn_"):
            raise ValueError("Поле inn должно содержать ИНН")
    if ogrn is not None:
        ogrn, kind = validate_identifier(ogrn)
        if kind not in {"ogrn", "ogrnip"}:
            raise ValueError("Поле ogrn должно содержать ОГРН/ОГРНИП")
    if not inn and not ogrn:
        if value.get("identifier"):
            return normalize_request(value["identifier"])
        raise ValueError("Нет ИНН/ОГРН")
    if inn and ogrn and (len(inn), len(ogrn)) not in {(10, 13), (12, 15)}:
        raise ValueError("Типы ИНН и ОГРН не совпадают")
    return {"identifier": ogrn or inn, "inn": inn, "ogrn": ogrn}


def compact_document(document):
    if not isinstance(document, dict):
        raise ValueError("JSON парсера должен быть объектом")
    if document.get("schema_version") == "1.0":
        return compact_result(document)
    if document.get("schema_version") != "compact-1.0" or not isinstance(document.get("entities"), list):
        raise ValueError("Ожидается JSON парсера 1.0 или compact-1.0")
    return document


def project(document, request, source_file=None):
    """One requested registration -> one row; incomplete results retain missing values."""
    request = normalize_request(request)
    result = compact_document(document) if document is not None else {}
    entities = result.get("entities", [])
    matches = [e for e in entities if e.get("ogrn") == request["ogrn"]] if request["ogrn"] else [
        e for e in entities if e.get("inn") == request["inn"]]
    if len(matches) > 1:
        raise ValueError("Несколько регистраций: передайте точный ОГРН")
    if entities and not matches:
        raise ValueError("Идентификатор результата не совпадает с запросом")
    entity = matches[0] if matches else {}
    if request["inn"] and entity.get("inn") and request["inn"] != entity["inn"]:
        raise ValueError("ИНН результата не совпадает с входной парой ИНН/ОГРН")
    row = dict.fromkeys(TEMPLATE_COLUMNS + EXTRA_COLUMNS)
    provenance, invalid = {}, []

    def put(field, value, origin):
        if field in NUMERIC:
            normalized = number(value)
        elif field in DATE_FEATURES:
            normalized = clean_date(value, DATE_FEATURES[field].startswith("months"))
        else:
            normalized = clean_text(value)
        if value is not None and normalized is None:
            invalid.append(field)
        row[field] = normalized
        if normalized is not None:
            provenance[field] = origin

    for field, value in {
        "meta_record_type": "company_snapshot", "meta_source_file": source_file,
        "meta_features_as_of": result.get("checked_at"),
        "meta_inn": entity.get("inn") or request["inn"],
        "meta_ogrn": entity.get("ogrn") or request["ogrn"],
        "meta_parser_status": result.get("status", "not_processed"),
        "meta_last_attempt_status": result.get("status", "not_processed"),
        "meta_answers_state": "not_collected", "meta_label_status": "no_loan_history",
    }.items():
        put(field, value, "parser_or_request_metadata")
    for source in ("egrul", "transparent_business", "girbo"):
        put("meta_" + source + "_status", (result.get("sources", {}).get(source) or {}).get("status"),
            "parser.sources." + source + ".status")
    for field, key in {"meta_company_name": "name", "entity_type": "entity_type", "registered_on": "registered_on",
                       "registry_status": "status", "main_okved": "main_okved", "capital_rub": "capital_rub"}.items():
        put(field, entity.get(key), "parser.entity." + key)
    founders = entity.get("founders")
    if isinstance(founders, list):
        put("founders_count", len(founders), "len(parser.entity.founders)")
        shares = [number(founder.get("share_percent")) for founder in founders]
        if shares and all(x is not None for x in shares):
            put("largest_founder_share_pct", max(shares), "max(parser.entity.founders.share_percent)")
    for field, key in (("employees_year", "year"), ("employees_count", "count")):
        put(field, (entity.get("employees") or {}).get(key), "parser.entity.employees." + key)
    years = {}
    for values in entity.get("financials", []):
        year = number(values.get("year"))
        if not isinstance(year, int) or not 1900 <= year <= 9999:
            continue
        if year in years and years[year] != values:
            raise ValueError("Противоречивые финансовые записи за один год")
        years[year] = values
    if years:
        latest = max(years)
        for offset in range(3):
            year = latest - offset
            put(f"financial_year_t{offset}", year, "latest_available_financial_year_minus_offset")
            for dest, key in (("income", "income"), ("expenses", "expenses"), ("taxes_paid", "taxes_paid")):
                put(f"{dest}_t{offset}_raw", years.get(year, {}).get(key), f"parser.entity.financials[{year}].{key}")
        # Current parser contract: PB aggregate income/expenses/taxes are in rubles.
        put("meta_financial_amount_unit", "rub", "parser_contract")
    regime = entity.get("tax_regime") or {}
    regimes = sorted({value for raw in regime.get("regimes", []) if (value := clean_text(raw))})
    if regimes:
        row["tax_regime"] = regimes
        provenance["tax_regime"] = "parser.entity.tax_regime.regimes"
    put("tax_regime_period", regime.get("period"), "parser.entity.tax_regime.period")
    debt = entity.get("tax_debt") or {}
    for field, key in (("tax_debt_status", "status"), ("tax_debt_amount_rub", "amount"), ("tax_debt_period", "period")):
        put(field, debt.get(key), "parser.entity.tax_debt." + key)
    for field, key in (("meta_registry_data_date", "registry"), ("meta_financials_data_date", "financials"),
                       ("meta_employees_data_date", "employees"), ("meta_taxes_paid_data_date", "taxes_paid"),
                       ("meta_tax_debt_data_date", "tax_debt")):
        put(field, (entity.get("data_dates") or {}).get(key), "parser.entity.data_dates." + key)
    # Questionnaire, loan outcomes and monthly averages require their own records.
    # Annual totals, GIR BO revenue, recommended products and closed status are not substitutes.
    return row, {"identifier": request["identifier"], "source_file": source_file,
                 "field_sources": provenance, "invalid_or_unavailable_values": invalid}


def categories(value):
    return value if isinstance(value, list) else [value] if value is not None else []


def category_column(field, value):
    return field + "__value_" + quote(value, safe="")


def build_schema(rows, columns):
    vocabulary = {field: sorted({value for row in rows for value in categories(row[field])})
                  for field in columns if field in CATEGORICAL}
    encoded, features = [], []
    for field in columns:
        names = ([category_column(field, value) for value in vocabulary[field]] + [field + "__unseen"]
                 if field in CATEGORICAL else [field])
        names += [field + "_missing"]
        encoded.extend(names)
        if is_feature(field):
            features.extend(names)
    return {"schema_version": "scoring-encoding-1.0", "source_columns": columns,
            "categorical_values": vocabulary, "encoded_columns": encoded, "feature_columns": features,
            "date_encoding": DATE_FEATURES, "multi_value_categorical": ["tax_regime"],
            "missing_policy": {"fill": 0, "missing": 1, "present": 0},
            "target_column": "target_default_12m", "target_missing_column": "target_default_12m_missing",
            "training_row_filter": "target_default_12m_missing == 0",
            "notes": ["Fit category vocabulary on training rows before model evaluation; reuse this schema for validation/inference.",
                      "Metadata, outcomes, target and their missing flags are excluded from feature_columns.",
                      "Tax regimes may have several active indicators; unknown categories use __unseen with missing=0."]}


def encode(row, schema):
    out = {}
    for field in schema["source_columns"]:
        value = row[field]
        absent = value is None
        if field in CATEGORICAL:
            observed = set(categories(value))
            known = schema["categorical_values"][field]
            for category in known:
                out[category_column(field, category)] = int(category in observed)
            out[field + "__unseen"] = int(bool(observed - set(known)))
        elif field in DATE_FEATURES and not absent:
            if DATE_FEATURES[field].startswith("months"):
                year, month = map(int, value.split("-"))
                out[field] = (year - 1970) * 12 + month - 1
            else:
                out[field] = (date.fromisoformat(value) - date(1970, 1, 1)).days
        else:
            out[field] = 0 if absent else value
        out[field + "_missing"] = int(absent)
    return out


def raw_row(row, columns):
    out = {}
    for field in columns:
        value = row[field]
        out[field] = 0 if value is None else "|".join(value) if isinstance(value, list) else value
        out[field + "_missing"] = int(value is None)
    return out


def load_encoder(path, columns):
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise ValueError("Схема one-hot должна быть JSON-объектом")
    expected = build_schema([], columns)
    if saved.get("schema_version") != expected["schema_version"] or saved.get("source_columns") != columns:
        raise ValueError("Несовместимая схема one-hot")
    values = saved.get("categorical_values")
    if not isinstance(values, dict) or set(values) != set(expected["categorical_values"]):
        raise ValueError("Некорректный словарь категорий")
    probe_rows = []
    for field, vocabulary in values.items():
        if not isinstance(vocabulary, list) or any(not isinstance(v, str) for v in vocabulary) or len(set(vocabulary)) != len(vocabulary):
            raise ValueError("Некорректные категории: " + field)
        for value in vocabulary:
            probe = dict.fromkeys(columns)
            probe[field] = [value] if field == "tax_regime" else value
            probe_rows.append(probe)
    rebuilt = build_schema(probe_rows, columns)
    for key in ("categorical_values", "encoded_columns", "feature_columns", "date_encoding",
                "missing_policy", "multi_value_categorical", "target_column", "target_missing_column"):
        if saved.get(key) != rebuilt[key]:
            raise ValueError("Несогласованная схема кодирования: " + key)
    return saved


def export_tables(rows, provenance, output, *, template=None, encoder_schema=None, additional_columns=()):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    columns = read_template(template) + EXTRA_COLUMNS + list(additional_columns)
    if len(columns) != len(set(columns)):
        raise ValueError("Duplicate export columns")
    schema = load_encoder(encoder_schema, columns) if encoder_schema else build_schema(rows, columns)

    raw_columns = columns + [field + "_missing" for field in columns]
    feature_names = set(schema["feature_columns"])
    paths = {}
    for filename, names, records in (
        ("dataset_raw.csv", raw_columns, (raw_row(row, columns) for row in rows)),
        ("dataset.csv", schema["encoded_columns"], (encode(row, schema) for row in rows)),
        ("features.csv", schema["feature_columns"], ({k: v for k, v in encode(row, schema).items()
                                                   if k in feature_names} for row in rows)),
    ):
        destination = output / filename
        temporary = destination.with_suffix(".csv.part")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=names)
            writer.writeheader()
            writer.writerows(records)
        temporary.replace(destination)
        with destination.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        paths[filename] = {"rows": len(rows), "columns": len(names), "sha256": digest}
    atomic_json(output / "encoder.json", schema)
    temporary = output / "field_sources.jsonl.part"
    with temporary.open("w", encoding="utf-8") as stream:
        for item in provenance:
            stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(output / "field_sources.jsonl")
    return {"rows": len(rows), "files": paths, "labelled_rows": sum(row["target_default_12m"] is not None for row in rows),
            "missing_counts": {field: sum(row[field] is None for row in rows) for field in columns}}
