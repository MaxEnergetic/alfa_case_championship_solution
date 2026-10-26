"""Кредитная логика: продукт, расчёт платежа, стоп-факторы и решение по баллу скоринга.

Скоринг (сервис scoring) оценивает риск; здесь балл превращается в исход с учётом продукта,
суммы, срока и выручки. Параметры продуктов синтетические, близкие к рыночным.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

PRODUCTS = {
    "single": {"title": "Кредит одной суммой", "rate": 22, "min_amount": 300_000, "max_amount": 30_000_000, "min_term": 3, "max_term": 60},
    "secured": {"title": "Кредит с залогом", "rate": 19, "min_amount": 500_000, "max_amount": 150_000_000, "min_term": 12, "max_term": 120},
    "line": {"title": "Возобновляемая кредитная линия", "rate": 23, "min_amount": 300_000, "max_amount": 30_000_000, "min_term": 6, "max_term": 18},
    "overdraft": {"title": "Овердрафт", "rate": 24, "min_amount": 300_000, "max_amount": 20_000_000, "min_term": 2, "max_term": 12},
}
AMOUNT_STEP = 100_000
REJECT_BELOW, MANUAL_BELOW, HIGH_SCORE = 35, 50, 70
PAYMENT_SHARE_HIGH, PAYMENT_SHARE_MEDIUM = 0.30, 0.20


def product_key(form: dict) -> str:
    if form.get("moneyUse") == "bridge" or form.get("purpose") == "gap":
        return "overdraft"
    if form.get("moneyUse") == "parts":
        return "line"
    if float(form.get("amount") or 0) > 30_000_000 or form.get("collateral") in ("property", "vehicle"):
        return "secured"
    return "single"


def annuity(amount: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 1200
    months = max(1, int(months))
    return amount / months if r == 0 else amount * r / (1 - (1 + r) ** -months)


@dataclass
class Quote:
    key: str
    amount: int
    months: int
    max_months: int

    @property
    def product(self) -> dict:
        return PRODUCTS[self.key]

    @property
    def payment(self) -> float:
        return annuity(self.amount, self.product["rate"], self.months)


def quote(form: dict) -> Quote:
    key = product_key(form)
    p = PRODUCTS[key]
    amount = int(min(p["max_amount"], max(p["min_amount"], float(form.get("amount") or p["min_amount"]))))
    months = int(min(p["max_term"], max(p["min_term"], int(form.get("term") or p["min_term"]))))
    max_months = int(min(p["max_term"], max(months, int(form.get("maxTerm") or months))))
    return Quote(key, amount, months, max_months)


def age_months(registered_on: date, today: date) -> int:
    return (today.year - registered_on.year) * 12 + today.month - registered_on.month - (today.day < registered_on.day)


def stop_factor(egrul: dict, fssp: dict, today: date) -> Optional[str]:
    status = (egrul.get("status") or "").lower()
    if "ликвидац" in status:
        return "Компания находится в стадии ликвидации."
    if "банкрот" in status:
        return "В отношении компании начата процедура банкротства."
    if age_months(date.fromisoformat(egrul["registered_on"]), today) < 3:
        return "Бизнесу меньше трёх месяцев."
    if float(fssp.get("debt_rub") or 0) > 1_000_000:
        return "Задолженность у приставов превышает 1 млн ₽."
    return None


def offer(kind: str, q: Quote, amount: int, months: int, reason: str) -> dict:
    return {"kind": kind, "amount": amount, "months": months, "rate": q.product["rate"],
            "payment": round(annuity(amount, q.product["rate"], months)), "reason": reason}


def decide(total: float, q: Quote, monthly_revenue: float) -> dict:
    """Балл → исход. Контрпредложение: сначала срок (до максимума клиента), потом сумма."""
    if total < REJECT_BELOW:
        return {"kind": "declined", "reason": "Итоговый балл ниже порога для предложения. Можно подать заявку снова, когда снизится нагрузка или появится отчётность."}
    if total < MANUAL_BELOW:
        return {"kind": "manual_review", "reason": "Нужна дополнительная проверка специалистом банка."}

    share = PAYMENT_SHARE_HIGH if total >= HIGH_SCORE else PAYMENT_SHARE_MEDIUM
    limit = share * monthly_revenue
    rate = q.product["rate"]
    if q.payment <= limit:
        return offer("approved", q, q.amount, q.months, f"Платёж укладывается в {round(share * 100)}% месячной выручки.")

    for months in range(q.months + 1, q.max_months + 1):
        if annuity(q.amount, rate, months) <= limit:
            return offer("counteroffer", q, q.amount, months,
                         f"На {q.months} мес. платёж выше {round(share * 100)}% выручки. Та же сумма доступна на более длинный срок.")

    best = int(limit / annuity(1, rate, q.max_months)) // AMOUNT_STEP * AMOUNT_STEP
    best = min(best, q.amount - AMOUNT_STEP)
    if best >= q.product["min_amount"]:
        return offer("counteroffer", q, best, q.max_months,
                     f"Запрошенная сумма даёт платёж выше {round(share * 100)}% выручки. При меньшей сумме платёж будет комфортным. "
                     "Чтобы получить запрошенную сумму, увеличьте срок или добавьте залог.")
    return {"kind": "declined", "reason": "При текущей выручке платёж оказался бы слишком высоким даже на минимальной сумме."}
