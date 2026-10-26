"""Локальные моки внешних источников: ЕГРЮЛ/ЕГРИП, ФНС («Прозрачный бизнес» + ГИР БО), ФССП/КАД и БКИ.

Контракты упрощены, но повторяют смысл реальных интеграций. Все данные синтетические.
Для неизвестного ИНН (с корректной контрольной суммой) отдаётся детерминированный синтетический профиль
без отчётности — это ведёт заявку в ветку «Нужны документы».
"""
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam

DATA = json.loads((Path(__file__).parent / "data" / "companies.json").read_text(encoding="utf-8"))["companies"]
INN_PATTERN = r"^\d{10}$|^\d{12}$"

app = FastAPI(
    title="Моки внешних источников",
    version="1.0.0",
    description="ЕГРЮЛ/ЕГРИП, ФНС, ФССП и БКИ для демонстрации. Только синтетические данные.",
)


def valid_inn(inn: str) -> bool:
    d = [int(c) for c in inn]
    if len(d) == 10:
        return sum(w * x for w, x in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10 == d[9]
    if len(d) == 12:
        c11 = sum(w * x for w, x in zip([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10
        c12 = sum(w * x for w, x in zip([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10
        return c11 == d[10] and c12 == d[11]
    return False


def generated(inn: str) -> dict:
    """Синтетический профиль для любого корректного ИНН вне справочника."""
    seed = int(hashlib.sha256(inn.encode()).hexdigest(), 16)
    is_ip = len(inn) == 12
    year = 2018 + seed % 6
    name = ("ИП «Демо " if is_ip else "ООО «Демо ") + inn[-4:] + "»"
    return {
        "egrul": {
            "inn": inn, "ogrn": ("3" if is_ip else "1") + inn.rjust(14, "0")[-14:], "name": name,
            "legal_type": "ИП" if is_ip else "ООО", "status": "Действующий" if is_ip else "Действующая",
            "registered_on": f"{year}-0{1 + seed % 9}-1{seed % 9}", "region": "Москва",
            "okved": "46.90", "okved_name": "Оптовая торговля", "director": "Смирнов Алексей Викторович",
        },
        "fns": {"tax_debt_rub": 0, "employees": 5, "tax_regime": "УСН", "statements": None},
        "fssp": {"debt_rub": 0, "court_cases_as_defendant": 0},
        "bki": {"credit_history": "clear", "active_loans": 0, "monthly_payments_rub": 0},
        "declared_monthly_revenue_rub": 650000,
        "synthetic_fallback": True,
    }


def record(inn: str) -> dict:
    if not valid_inn(inn):
        raise HTTPException(422, {"code": "invalid_inn", "message": "Некорректная контрольная сумма ИНН"})
    return DATA.get(inn) or generated(inn)


@app.get("/health", tags=["Сервис"])
def health():
    return {"status": "ok", "companies": len(DATA)}


@app.get("/egrul/{inn}", tags=["ЕГРЮЛ/ЕГРИП"], summary="Регистрационные сведения")
def egrul(inn: str = PathParam(pattern=INN_PATTERN)):
    r = record(inn)
    return {**r["egrul"], "synthetic_fallback": r.get("synthetic_fallback", False)}


@app.get("/fns/{inn}", tags=["ФНС"], summary="«Прозрачный бизнес» и бухотчётность (ГИР БО)")
def fns(inn: str = PathParam(pattern=INN_PATTERN)):
    r = record(inn)
    return {"inn": inn, **r["fns"], "declared_monthly_revenue_rub": r.get("declared_monthly_revenue_rub")}


@app.get("/fssp/{inn}", tags=["ФССП / КАД"], summary="Исполнительные производства и суды")
def fssp(inn: str = PathParam(pattern=INN_PATTERN)):
    return {"inn": inn, **record(inn)["fssp"]}


@app.get("/bki/{inn}", tags=["БКИ"], summary="Кредитная история (только после согласия клиента)")
def bki(inn: str = PathParam(pattern=INN_PATTERN)):
    return {"inn": inn, **record(inn)["bki"]}
