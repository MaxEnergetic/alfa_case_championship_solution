"""Сервис скоринга: ML-модель финансовой устойчивости + прозрачные правила → балл 0–100.

Модель и подготовка признаков берутся из `backend/fns_parser/scoring` (модель Макса, адаптер признаков бэка)
без копирования: тот же `model.joblib`, тот же `build_features`. Скоринг оценивает риск; решение
(одобрить / контрпредложение / ручная проверка / отказ) принимает кредитная логика в сервисе заявок.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from fns_parser.scoring.service import bundle_info, load_model
from fns_parser.scoring.upstream.core import build_features

MODEL_POINTS, MODEL_THRESHOLD = 40, 0.78

app = FastAPI(title="Сервис скоринга", version="1.0.0",
              description="Балл риска 0–100: ML-модель финансовой устойчивости (40) + правила (60).")


class Statements(BaseModel):
    year: int
    units: Literal["thousand_RUB"] = "thousand_RUB"
    lines: dict[str, float]


class ScoreRequest(BaseModel):
    inn: str = Field(pattern=r"^\d{10}$|^\d{12}$")
    registered_on: date
    okved: str
    statements: Optional[Statements] = None
    credit_history: Literal["clear", "closed_overdue", "current_overdue"] = "clear"
    tax_debt_rub: float = 0
    court_cases_as_defendant: int = 0
    fssp_debt_rub: float = 0
    monthly_revenue_rub: float = Field(ge=0)
    current_payments_rub: float = Field(0, ge=0)
    new_payment_rub: float = Field(ge=0)


class Factor(BaseModel):
    code: str
    name: str
    value: float
    max: int
    note: str


class ModelResult(BaseModel):
    used: bool
    probability: Optional[float] = None
    threshold: float = MODEL_THRESHOLD
    version: Optional[str] = None
    reason: Optional[str] = None


class ScoreResponse(BaseModel):
    total: float
    factors: list[Factor]
    model: ModelResult
    load_ratio: Optional[float]


def today() -> date:
    return datetime.now(timezone(timedelta(hours=3))).date()


def model_probability(req: ScoreRequest) -> ModelResult:
    if req.statements is None:
        return ModelResult(used=False, reason="Нет бухгалтерской отчётности")
    if len(req.inn) != 10:
        return ModelResult(used=False, reason="Модель обучена только на юрлицах")
    snapshot = {
        "inn": req.inn, "year": req.statements.year,
        "source": {"units": req.statements.units, "available_by": f"{req.statements.year + 1}-04-01", "dataset": "mock_girbo"},
        "row": {"inn": req.inn, "creation_date": req.registered_on.isoformat(), "okved": req.okved,
                "filed": 1, "financial": 0, "imputed": 0, "outlier": 0, "articulated": 1,
                **{"line_" + code: value for code, value in req.statements.lines.items()}},
    }
    features = build_features(snapshot, today())
    if not features["can_predict"]:
        return ModelResult(used=False, reason="Недостаточно данных: " + ", ".join(features["quality_issues"]))
    import pandas as pd
    model, code = load_model()
    probability = float(model.predict_proba(code.make_features(pd.DataFrame([features["features"]])))[0, 1])
    return ModelResult(used=True, probability=round(probability, 4), version=bundle_info()["id"])


def age_months(registered_on: date) -> int:
    t = today()
    return (t.year - registered_on.year) * 12 + t.month - registered_on.month - (t.day < registered_on.day)


def years_text(n: int) -> str:
    word = "год" if n % 10 == 1 and n % 100 != 11 else "года" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "лет"
    return f"{n} {word}"


def score(req: ScoreRequest) -> ScoreResponse:
    factors: list[Factor] = []
    model = model_probability(req)
    if model.used:
        p = model.probability
        points = MODEL_POINTS if p >= MODEL_THRESHOLD else MODEL_POINTS * max(0.0, p) / MODEL_THRESHOLD
        factors.append(Factor(code="financial_stability", name="Финансовая устойчивость", value=round(points, 1), max=40,
                              note=f"ML-модель, скор {p:.2f}".replace(".", ",")))

    history = {"clear": (20, "Просрочек нет"), "closed_overdue": (10, "Были закрытые просрочки"),
               "current_overdue": (0, "Есть текущие просрочки")}[req.credit_history]
    factors.append(Factor(code="payment_discipline", name="Платёжная дисциплина", value=history[0], max=20, note=history[1]))

    ratio = (req.current_payments_rub + req.new_payment_rub) / req.monthly_revenue_rub if req.monthly_revenue_rub > 0 else None
    load = 0 if ratio is None else 15 if ratio < 0.3 else 8 if ratio <= 0.5 else 0
    factors.append(Factor(code="debt_load", name="Платёжная нагрузка", value=load, max=15,
                          note="Нет данных о выручке" if ratio is None else f"{round(ratio * 100)}% выручки"))

    months = age_months(req.registered_on)
    age = 10 if months >= 36 else 7 if months >= 12 else 4 if months >= 6 else 1 if months >= 3 else 0
    factors.append(Factor(code="business_age", name="Возраст бизнеса", value=age, max=10,
                          note=years_text(months // 12) if months >= 12 else f"{months} мес."))

    tax = 0 if req.tax_debt_rub > 100_000 else 5 if req.tax_debt_rub > 0 else 10
    factors.append(Factor(code="tax_debt", name="Налоговый долг", value=tax, max=10,
                          note="Нет" if req.tax_debt_rub == 0 else f"{round(req.tax_debt_rub):,} ₽".replace(",", " ")))

    legal = 0 if req.court_cases_as_defendant or req.fssp_debt_rub else 5
    factors.append(Factor(code="courts_bailiffs", name="Суды и приставы", value=legal, max=5,
                          note="Ограничений нет" if legal else "Есть записи"))

    total = sum(f.value for f in factors)
    if not model.used:  # нет отчётности: остальные 60 баллов масштабируются до 100
        total = total * 100 / 60
    return ScoreResponse(total=round(min(100, max(0, total)), 1), factors=factors, model=model,
                         load_ratio=None if ratio is None else round(ratio, 4))


@app.get("/health", tags=["Сервис"])
def health():
    load_model()
    return {"status": "ok", "model": bundle_info()["id"]}


@app.post("/api/v1/score", response_model=ScoreResponse, tags=["Скоринг"], summary="Посчитать балл заявки")
def score_endpoint(req: ScoreRequest) -> ScoreResponse:
    return score(req)
