"""Кредитная логика: выбор продукта, стоп-факторы, исходы и контрпредложение."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import decision as credit  # noqa: E402

BASE = {"purpose": "equipment", "moneyUse": "all", "amount": 3_000_000, "term": 18, "collateral": "none"}


def test_product_selection():
    assert credit.product_key({**BASE, "moneyUse": "bridge"}) == "overdraft"
    assert credit.product_key({**BASE, "purpose": "gap"}) == "overdraft"
    assert credit.product_key({**BASE, "moneyUse": "parts"}) == "line"
    assert credit.product_key({**BASE, "collateral": "property"}) == "secured"
    assert credit.product_key({**BASE, "amount": 40_000_000}) == "secured"
    assert credit.product_key(BASE) == "single"


def test_thresholds():
    q = credit.quote(BASE)
    assert credit.decide(34.9, q, 10_000_000)["kind"] == "declined"
    assert credit.decide(35, q, 10_000_000)["kind"] == "manual_review"
    assert credit.decide(80, q, 10_000_000)["kind"] == "approved"


def test_counteroffer_reduces_amount_within_client_term():
    result = credit.decide(83.3, credit.quote(BASE), 450_000)
    assert (result["kind"], result["amount"], result["months"]) == ("counteroffer", 2_000_000, 18)
    assert result["payment"] <= 0.3 * 450_000


def test_counteroffer_extends_term_first():
    result = credit.decide(83.3, credit.quote({**BASE, "maxTerm": 36}), 450_000)
    assert (result["kind"], result["amount"], result["months"]) == ("counteroffer", 3_000_000, 29)


def test_medium_score_uses_stricter_payment_share():
    q = credit.quote(BASE)
    assert credit.decide(60, q, 800_000)["kind"] == "counteroffer"   # 197 тыс. > 20% от 800 тыс.
    assert credit.decide(75, q, 800_000)["kind"] == "approved"       # 197 тыс. < 30% от 800 тыс.


def test_stop_factors():
    today = date(2026, 10, 26)
    ok = {"status": "Действующая", "registered_on": "2020-01-01"}
    assert credit.stop_factor(ok, {"debt_rub": 0}, today) is None
    assert "ликвидац" in credit.stop_factor({**ok, "status": "В стадии ликвидации"}, {}, today)
    assert "трёх месяцев" in credit.stop_factor({**ok, "registered_on": "2026-09-01"}, {}, today)
    assert "приставов" in credit.stop_factor(ok, {"debt_rub": 2_000_000}, today)
