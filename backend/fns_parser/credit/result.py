"""Combined JSON projection with explicit source priority and no synthetic financials."""

from dataclasses import asdict
from datetime import date

from .dialogue import replay
from .models import (ApplicationData, BankRelationship, Borrower, ExistingLoan, Financials,
                     Financing, Security, SessionView)
from .registry import authoritative_answers, selected_entity
from .rules import BUSINESS_FORMS, PRODUCTS, recommend

GROUPS = {"borrower": Borrower, "financing": Financing, "existing_loan": ExistingLoan,
          "bank": BankRelationship, "security": Security, "financials": Financials}
ALIASES = {"amount_rub": "amount", "asset_cost_rub": "asset_cost", "own_amount_rub": "own_amount",
           "exists": "existing_loan", "owner": "loan_owner", "bank_and_goal": "loan_bank",
           "balance_rub": "loan_balance", "payment_monthly_rub": "loan_payment", "remaining_months": "loan_months",
           "revenue_monthly_rub": "revenue", "free_cash_monthly_rub": "free_cash", "debt_payments_monthly_rub": "debt_payments"}
FIELD_PATHS = {ALIASES.get(field, field): f"{group}.{field}" for group, model in GROUPS.items() for field in model.model_fields}
FIELD_PATHS["inn"] = "company.inn"


def assessment(answers, record):
    result = recommend(answers)
    issues, notes = result["issues"], result["notes"]
    entity = selected_entity(record)
    if answers.get("form") in BUSINESS_FORMS:
        if not answers.get("inn"):
            issues.append("Q13: ИНН неизвестен; сведения бизнеса не подтверждены.")
        elif not entity or not authoritative_answers(record):
            issues.append("Q13: получите сведения реестра и выберите нужный ОГРН / ОГРНИП.")
    if answers.get("goal") in {"investment", "property", "vehicle", "startup"}:
        if answers.get("own_funds") == "unknown" or (answers.get("own_funds") == "yes" and answers.get("own_amount") is None):
            issues.append("Q28: нужно уточнить сумму собственных средств для бюджета покупки / запуска.")
    if (answers.get("goal") == "vehicle" or answers.get("asset") == "vehicle") and not answers.get("vehicle"):
        issues.append("Q08: нужны тип и состояние транспорта / спецтехники.")
    if answers.get("goal") == "investment" and answers.get("asset") == "unknown":
        issues.append("Q06a: уточните приобретаемый объект.")
    if answers.get("goal") == "refinance" and answers.get("existing_loan") == "yes" and not answers.get("loan_bank"):
        issues.append("Q09: уточните банк и цель замены кредита.")
    if answers.get("goal") == "contract" and not answers.get("contract_details"):
        issues.append("Q10b: нужны параметры контракта / гарантии.")
    if answers.get("seasonal") == "yes" and any(answers.get(k) is None for k in ("revenue", "revenue_period", "free_cash")):
        notes.append("Q29–Q30: финансовые сведения неполны; платёжеспособность не рассчитана.")
    if issues:
        result["status"] = "R02"
    return {"status": result["status"], "rules_as_of": result["rules_as_of"], "is_bank_decision": False,
            "candidates": [{**asdict(c), "name": PRODUCTS[c.code]} for c in result["candidates"]],
            "issues": list(dict.fromkeys(issues)), "notes": list(dict.fromkeys(notes))}


def view(record):
    progress = replay(record["entries"], date.fromisoformat(record["date"]))
    declared = progress.answers
    facts = authoritative_answers(record)
    effective = {**declared, **facts}
    choice_ids = {q.id for q in progress.answered if q.type == "choice"}

    def exposed(key):
        value = effective.get(key)
        if key in choice_ids and value == "unknown":
            return None
        return value.isoformat() if isinstance(value, date) else value

    data = {group: {field: exposed(ALIASES.get(field, field)) for field in model.model_fields}
            for group, model in GROUPS.items()}
    registry = record.get("registry") or {}
    data["company"] = {"inn": exposed("inn"), "selected_ogrn": record.get("selected_ogrn"),
                       "lookup_status": registry.get("status", "not_requested"), "checked_at": registry.get("checked_at"),
                       "sources": registry.get("sources", {}), "entities": registry.get("entities", [])}
    states = {FIELD_PATHS[q.id]: "unknown" if exposed(q.id) is None else "provided" for q in progress.answered}
    for key in ("reuse", "own_amount"):
        if key in effective and FIELD_PATHS[key] not in states:
            states[FIELD_PATHS[key]] = "derived"
    sources, conflicts = {}, []
    for key, value in facts.items():
        path = FIELD_PATHS[key]
        states[path] = "registry"
        sources[path] = {"source": "fns_parser", "checked_at": registry.get("checked_at"), "ogrn": record["selected_ogrn"]}
        if key in declared and declared[key] not in (None, "unknown", value):
            conflicts.append({"field": path, "user_value": declared[key], "registry_value": value, "chosen_source": "fns_parser"})
    return SessionView(schema_version="credit-application-1.0", session_id=record["id"], revision=record["revision"],
                       state="collecting" if progress.next_question else "ready",
                       created_at=record["created_at"], updated_at=record["updated_at"],
                       next_question=progress.next_question, data=ApplicationData.model_validate(data),
                       answer_states=states, field_sources=sources, conflicts=conflicts,
                       scoring_job_id=record.get('scoring_job_id'), scoring=record.get('scoring'),
                       recommendation=None if progress.next_question else assessment(effective, record))
