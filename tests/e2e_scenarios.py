"""Сквозные сценарии по демо-компаниям через публичный API (тот же путь, что проходит клиент в кабинете).

Запуск при поднятом стенде:  python tests/e2e_scenarios.py http://localhost:8080
Каждый сценарий: вход клиента → черновик → анкета → отправка → (выписка) → ожидание решения → проверка исхода.
"""
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
PASSWORD = "alfa2026"
PDF = b"%PDF-1.4\n% synthetic statement for demo\n%%EOF\n"

SCENARIOS = [
    # ИНН, анкета, ожидаемый исход, проверка условий
    ("9900000017", {"purpose": "equipment", "moneyUse": "all", "amount": 3_000_000, "term": 24, "collateral": "none"}, "approved", None),
    ("990000000299", {"purpose": "equipment", "moneyUse": "all", "amount": 3_000_000, "term": 18, "collateral": "none"}, "counteroffer",
     lambda d: d["amount"] == 2_000_000 and d["months"] == 18),
    ("990000000299", {"purpose": "equipment", "moneyUse": "all", "amount": 3_000_000, "term": 18, "maxTerm": 36, "collateral": "none"},
     "counteroffer", lambda d: d["amount"] == 3_000_000 and d["months"] == 29),
    ("9900000031", {"purpose": "stock", "moneyUse": "all", "amount": 3_000_000, "term": 24, "collateral": "none"}, "manual_review", None),
    ("990000000436", {"purpose": "stock", "moneyUse": "all", "amount": 1_000_000, "term": 12, "collateral": "none"}, "approved", None),
    ("9900000063", {"purpose": "stock", "moneyUse": "all", "amount": 1_000_000, "term": 12, "collateral": "none"}, "declined", None),
]


def login(client: httpx.Client, email: str, role: str) -> None:
    token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD, "role": role}).raise_for_status().json()["token"]
    client.headers["Authorization"] = "Bearer " + token


def run(inn: str, form: dict, expected: str, check) -> str:
    with httpx.Client(base_url=BASE, timeout=15) as c:
        login(c, "client@demo.alfa.ru", "client")
        company = c.get(f"/api/v1/companies/{inn}").raise_for_status().json()
        app = c.post("/api/v1/applications", json={}).raise_for_status().json()
        full = {**form, "tin": inn, "applicantName": company["director"], "applicantRole": "owner",
                "monthlyRevenue": company["monthlyRevenue"], "currentPayments": 0, "consentBki": True, "consentPrivacy": True}
        c.put(f"/api/v1/applications/{app['id']}", json={"form": full, "wizardStage": "consent", "wizardIndex": 4, "company": company}).raise_for_status()
        c.post(f"/api/v1/applications/{app['id']}/submit").raise_for_status()
        for _ in range(80):
            app = c.get(f"/api/v1/applications/{app['id']}").raise_for_status().json()
            if app["status"] == "needs_docs":
                c.post(f"/api/v1/applications/{app['id']}/documents", files={"file": ("statement.pdf", PDF, "application/pdf")}).raise_for_status()
            elif app["status"] in ("approved", "counteroffer", "manual_review", "declined"):
                break
            time.sleep(0.5)
        decision = app.get("decision") or {}
        ok = app["status"] == expected and (check is None or check(decision))
        score = (decision.get("score") or {}).get("total")
        return f"{'OK  ' if ok else 'FAIL'} {inn} {company['name']}: {app['status']} (ожидали {expected}), балл {score}, " \
               f"сумма {decision.get('amount')}, срок {decision.get('months')}"


def manual_review_by_staff() -> str:
    """Ручная проверка: заявка «Мастерской» уходит сотруднику, он делает контрпредложение, клиент его видит."""
    form = {"purpose": "stock", "moneyUse": "all", "amount": 3_000_000, "term": 24, "collateral": "none"}
    with httpx.Client(base_url=BASE, timeout=15) as client, httpx.Client(base_url=BASE, timeout=15) as staff:
        login(client, "client@demo.alfa.ru", "client")
        company = client.get("/api/v1/companies/9900000031").raise_for_status().json()
        app = client.post("/api/v1/applications", json={}).raise_for_status().json()
        full = {**form, "tin": "9900000031", "applicantName": company["director"], "monthlyRevenue": company["monthlyRevenue"],
                "consentBki": True, "consentPrivacy": True}
        client.put(f"/api/v1/applications/{app['id']}", json={"form": full, "wizardStage": "consent", "company": company}).raise_for_status()
        client.post(f"/api/v1/applications/{app['id']}/submit").raise_for_status()
        for _ in range(60):
            if client.get(f"/api/v1/applications/{app['id']}").json()["status"] == "manual_review":
                break
            time.sleep(0.5)
        login(staff, "manager@demo.alfa.ru", "staff")
        queue = [a["id"] for a in staff.get("/api/v1/applications").json() if a["status"] == "manual_review"]
        staff.post(f"/api/v1/applications/{app['id']}/staff-decision",
                   json={"kind": "counteroffer", "amount": 1_500_000, "months": 24}).raise_for_status()
        seen = client.get(f"/api/v1/applications/{app['id']}").json()
        forbidden = client.post(f"/api/v1/applications/{app['id']}/staff-decision", json={"kind": "declined"}).status_code
        ok = app["id"] in queue and seen["status"] == "counteroffer" and seen["decision"]["amount"] == 1_500_000 and forbidden == 403
        return f"{'OK  ' if ok else 'FAIL'} 9900000031 ручная проверка → контрпредложение сотрудника {seen['decision'].get('amount')}; клиенту решение сотрудника недоступно ({forbidden})"


def blocked_by_stop_factor() -> str:
    with httpx.Client(base_url=BASE, timeout=15) as c:
        login(c, "client@demo.alfa.ru", "client")
        company = c.get("/api/v1/companies/9900000056").raise_for_status().json()
        ok = bool(company["stopReason"])
        return f"{'OK  ' if ok else 'FAIL'} 9900000056 {company['name']}: стоп-фактор «{company['stopReason']}»"


if __name__ == "__main__":
    results = [run(*s) for s in SCENARIOS] + [manual_review_by_staff(), blocked_by_stop_factor()]
    print("\n".join(results))
    sys.exit(0 if all(r.startswith("OK") for r in results) else 1)
