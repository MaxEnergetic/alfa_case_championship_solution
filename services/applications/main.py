"""Сервис заявок: кабинет клиента и рабочее место сотрудника.

Владеет заявкой и её статусами. Внешние сведения берёт из моков (ЕГРЮЛ/ЕГРИП, ФНС, ФССП, БКИ),
балл — из сервиса скоринга, решение принимает кредитная логика (decision.py).
Статусы двигает фоновый обработчик; при сбое соседнего сервиса шаг повторяется, заявка не теряется.
"""
import asyncio
import hashlib
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import decision as credit
from store import Store, now_iso

MOCKS_URL = os.getenv("MOCKS_URL", "http://localhost:8001")
SCORING_URL = os.getenv("SCORING_URL", "http://localhost:8002")
DB_PATH = os.getenv("DB_PATH", "./data/applications.db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "alfa2026")
STEP_SECONDS = float(os.getenv("STEP_SECONDS", "2.5"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TZ = timezone(timedelta(hours=3))

log = logging.getLogger("applications")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
store = Store(DB_PATH, DEMO_PASSWORD)
http: httpx.AsyncClient


def today() -> date:
    return datetime.now(TZ).date()


async def worker():
    while True:
        try:
            for row in store.due(time.time()):
                await advance(row)
        except Exception:  # обработчик не должен падать целиком из-за одной заявки
            log.exception("worker iteration failed")
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http
    http = httpx.AsyncClient(timeout=10)
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    await http.aclose()


app = FastAPI(
    title="Сервис заявок — Умный кредитный кабинет",
    version="1.0.0",
    description="Заявки малого бизнеса: черновик → подача → проверки → скоринг → решение → подписание. Данные синтетические.",
    lifespan=lifespan,
)
origins = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:8090").split(",") if o]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])


# ---------- внешние сервисы ----------
async def mock(source: str, inn: str) -> dict:
    r = await http.get(f"{MOCKS_URL}/{source}/{inn}")
    if r.status_code == 422:
        raise HTTPException(422, "Некорректный ИНН")
    r.raise_for_status()
    return r.json()


def company_card(egrul: dict, fns: dict, fssp: dict) -> dict:
    """Карточка компании в формате кабинета (только публичные сведения, без БКИ)."""
    reg = date.fromisoformat(egrul["registered_on"])
    statements = fns.get("statements")
    annual = statements["lines"].get("2110", 0) * 1000 if statements else None
    status = egrul.get("status", "")
    return {
        "tin": egrul["inn"], "ogrn": egrul.get("ogrn"), "name": egrul["name"], "legalType": egrul["legal_type"],
        "regDate": reg.strftime("%d.%m.%Y"), "registeredOn": egrul["registered_on"], "ageMonths": credit.age_months(reg, today()),
        "region": egrul.get("region"), "activity": egrul.get("okved_name"), "okved": egrul.get("okved"),
        "director": egrul.get("director"), "status": status, "liquidating": "ликвидац" in status.lower(),
        "bankrupt": "банкрот" in status.lower(), "hasReport": statements is not None, "annualRevenue": annual,
        "monthlyRevenue": round(annual / 12) if annual else fns.get("declared_monthly_revenue_rub") or 0,
        "taxDebt": fns.get("tax_debt_rub", 0), "bailiffDebt": fssp.get("debt_rub", 0),
        "courtCases": bool(fssp.get("court_cases_as_defendant")), "requiresDocs": statements is None,
        "syntheticFallback": bool(egrul.get("synthetic_fallback")), "stopReason": credit.stop_factor(egrul, fssp, today()),
    }


async def fetch_public(inn: str) -> dict:
    egrul, fns, fssp = await asyncio.gather(mock("egrul", inn), mock("fns", inn), mock("fssp", inn))
    return {"egrul": egrul, "fns": fns, "fssp": fssp}


# ---------- авторизация ----------
class User(BaseModel):
    email: str
    name: str
    role: Literal["client", "staff"]


def current_user(authorization: Optional[str] = Header(None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Нужна авторизация")
    user = store.session_user(authorization[7:])
    if not user:
        raise HTTPException(401, "Сессия истекла, войдите снова")
    return User(**user)


def staff_only(user: User = Depends(current_user)) -> User:
    if user.role != "staff":
        raise HTTPException(403, "Только для сотрудника банка")
    return user


class LoginIn(BaseModel):
    email: str
    password: str
    role: Optional[Literal["client", "staff"]] = None


@app.post("/api/v1/auth/login", tags=["Авторизация"], summary="Вход по демо-учётке")
def login(body: LoginIn):
    user = store.check_password(body.email.strip().lower(), body.password)
    if not user or (body.role and user["role"] != body.role):
        raise HTTPException(401, "Неверный логин или пароль")
    expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds").replace("+00:00", "Z")
    token = store.create_session(user["email"], expires)
    return {"token": token, "expiresAt": expires, "user": {"email": user["email"], "name": user["name"], "role": user["role"]}}


@app.get("/api/v1/auth/me", tags=["Авторизация"], response_model=User)
def me(user: User = Depends(current_user)):
    return user


@app.post("/api/v1/auth/logout", tags=["Авторизация"], status_code=204)
def logout(authorization: Optional[str] = Header(None), _: User = Depends(current_user)):
    store.drop_session(authorization[7:])


# ---------- компании ----------
@app.get("/api/v1/companies/{inn}", tags=["Компании"], summary="Карточка компании по ИНН (ЕГРЮЛ/ЕГРИП + ФНС + ФССП)")
async def company(inn: str, _: User = Depends(current_user)):
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(422, "ИНН — 10 или 12 цифр")
    try:
        return company_card(**await fetch_public(inn))
    except httpx.HTTPError:
        raise HTTPException(503, "Реестр временно недоступен, попробуйте ещё раз")


# ---------- заявки ----------
def view(row: dict, user: User) -> dict:
    data = row["data"]
    data = data if isinstance(data, dict) else json.loads(data)
    out = {**data, "id": row["id"], "displayId": row["display_id"], "ownerId": row["owner_email"], "status": row["status"],
           "createdAt": row["created_at"], "updatedAt": row["updated_at"],
           "history": [{"from": h["from_status"], "to": h["to_status"], "actor": h["actor"], "label": h["label"], "at": h["at"]}
                       for h in store.history(row["id"])],
           "consentLog": [{"type": c["type"], "text": c["text"], "user": c["user_email"], "at": c["at"]} for c in store.consents(row["id"])],
           "documents": store.documents(row["id"])}
    out.pop("public", None)
    if user.role == "client":  # сырые ответы источников, включая БКИ, клиенту не показываем
        out.pop("enrichment", None)
    return out


def load(app_id: str, user: User) -> dict:
    row = store.get(app_id)
    if not row or (user.role == "client" and row["owner_email"] != user.email):
        raise HTTPException(404, "Заявка не найдена")
    row["data"] = json.loads(row["data"])
    return row


def fresh(app_id: str, user: User) -> dict:
    return view(store.get(app_id), user)


@app.get("/api/v1/applications", tags=["Заявки"], summary="Мои заявки (сотрудник видит все)")
def list_applications(user: User = Depends(current_user)):
    return [view(r, user) for r in store.list_apps(None if user.role == "staff" else user.email)]


class DraftIn(BaseModel):
    form: dict = Field(default_factory=dict)
    wizardStage: str = "product"
    wizardIndex: int = 0
    company: Optional[dict] = None


ALLOWED_FORM = {"purpose", "otherGoal", "moneyUse", "amount", "term", "maxTerm", "collateral", "tin", "applicantName",
                "applicantRole", "powerOfAttorney", "monthlyRevenue", "revenueOverride", "currentPayments", "consentBki", "consentPrivacy"}


@app.post("/api/v1/applications", tags=["Заявки"], status_code=201, summary="Создать черновик")
def create_application(body: DraftIn | None = None, user: User = Depends(current_user)):
    if user.role != "client":
        raise HTTPException(403, "Заявку создаёт клиент")
    body = body or DraftIn()
    app_id = "req-" + uuid.uuid4().hex[:12]
    data = {"form": {k: v for k, v in body.form.items() if k in ALLOWED_FORM}, "wizardStage": body.wizardStage,
            "wizardIndex": body.wizardIndex, "company": body.company, "ownerName": user.name, "decision": None}
    store.create_application(app_id, str(random.randint(100000, 999999)), user.email, data, "Клиент", body.form.get("_label") or "Черновик создан")
    return fresh(app_id, user)


@app.get("/api/v1/applications/{app_id}", tags=["Заявки"])
def get_application(app_id: str, user: User = Depends(current_user)):
    load(app_id, user)
    return fresh(app_id, user)


@app.put("/api/v1/applications/{app_id}", tags=["Заявки"], summary="Сохранить черновик (автосохранение анкеты)")
def save_draft(app_id: str, body: DraftIn, user: User = Depends(current_user)):
    row = load(app_id, user)
    if row["status"] != "draft":
        raise HTTPException(409, "Отправленную заявку изменить нельзя")
    data = row["data"]
    data.update({"form": {k: v for k, v in body.form.items() if k in ALLOWED_FORM}, "wizardStage": body.wizardStage,
                 "wizardIndex": body.wizardIndex, "company": body.company})
    store.save_data(app_id, data)
    return fresh(app_id, user)


CONSENTS = [("bki", "Запрос кредитной истории, 218-ФЗ"), ("privacy", "Обработка персональных данных, 152-ФЗ")]


@app.post("/api/v1/applications/{app_id}/submit", tags=["Заявки"], summary="Отправить заявку")
async def submit(app_id: str, user: User = Depends(current_user)):
    row = load(app_id, user)
    data, form = row["data"], row["data"]["form"]
    if row["status"] != "draft":
        raise HTTPException(409, "Заявка уже отправлена")
    missing = [name for key, name in [("consentBki", "согласие на запрос в БКИ"), ("consentPrivacy", "согласие на обработку данных")] if not form.get(key)]
    if not form.get("tin"):
        missing.append("ИНН")
    if not str(form.get("applicantName") or "").strip():
        missing.append("ФИО заявителя")
    if float(form.get("monthlyRevenue") or 0) <= 0:
        missing.append("выручка")
    if missing:
        raise HTTPException(422, "Не заполнено: " + ", ".join(missing))
    try:
        public = await fetch_public(form["tin"])
    except httpx.HTTPError:
        raise HTTPException(503, "Реестр временно недоступен, попробуйте ещё раз")
    card = company_card(**public)
    if card["stopReason"]:
        raise HTTPException(422, card["stopReason"])
    q = credit.quote(form)
    data.update({"company": card, "public": public, "submittedAt": now_iso(),
                 "product": {"key": q.key, "title": q.product["title"], "rate": q.product["rate"]},
                 "applicant": {"name": form["applicantName"], "relation": form.get("applicantRole"),
                               "powerOfAttorney": bool(form.get("powerOfAttorney"))}})
    store.add_consents(app_id, CONSENTS, user.email)
    store.transition(app_id, "draft", "submitted", "Клиент", "Заявка отправлена", data, time.time() + STEP_SECONDS)
    return fresh(app_id, user)


@app.post("/api/v1/applications/{app_id}/documents", tags=["Заявки"], summary="Загрузить выписку (PDF)")
async def upload(app_id: str, file: UploadFile = File(...), user: User = Depends(current_user)):
    row = load(app_id, user)
    if row["status"] != "needs_docs":
        raise HTTPException(409, "Документы по этой заявке не запрашивались")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ")
    if not content.startswith(b"%PDF"):
        raise HTTPException(415, "Нужен файл PDF")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    doc_id = uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, doc_id + ".pdf")
    with open(path, "wb") as fh:
        fh.write(content)
    name = os.path.basename(file.filename or "statement.pdf")[:200]
    store.add_document({"id": doc_id, "application_id": app_id, "filename": name, "content_type": "application/pdf",
                        "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "path": path, "uploaded_at": now_iso()})
    data = row["data"]
    data["documentName"] = name
    store.transition(app_id, "needs_docs", "enrichment", "Клиент", "Выписка добавлена", data, time.time() + STEP_SECONDS)
    return fresh(app_id, user)


@app.post("/api/v1/applications/{app_id}/accept", tags=["Решение"], summary="Принять предложение")
def accept(app_id: str, user: User = Depends(current_user)):
    row = load(app_id, user)
    data = row["data"]; data["acceptedAt"] = now_iso()
    if not store.transition(app_id, ("approved", "counteroffer"), "accepted", "Клиент", "Предложение принято", data):
        raise HTTPException(409, "Нет активного предложения")
    return fresh(app_id, user)


@app.post("/api/v1/applications/{app_id}/reject", tags=["Решение"], summary="Отказаться от предложения")
def reject(app_id: str, user: User = Depends(current_user)):
    load(app_id, user)
    if not store.transition(app_id, ("approved", "counteroffer"), "rejected_by_client", "Клиент", "Клиент отказался от предложения"):
        raise HTTPException(409, "Нет активного предложения")
    return fresh(app_id, user)


@app.post("/api/v1/applications/{app_id}/revise", tags=["Решение"], status_code=201,
          summary="Изменить параметры: новая заявка-черновик с условиями предложения")
def revise(app_id: str, user: User = Depends(current_user)):
    row = load(app_id, user)
    data = row["data"]
    if not store.transition(app_id, ("approved", "counteroffer"), "rejected_by_client", "Клиент", "Клиент выбрал изменить параметры"):
        raise HTTPException(409, "Нет активного предложения")
    form = {**data["form"], "consentBki": False, "consentPrivacy": False}
    if data.get("decision"):
        form["amount"] = data["decision"].get("amount") or form.get("amount")
        form["term"] = data["decision"].get("months") or form.get("term")
        form["maxTerm"] = form["term"]
    new_id = "req-" + uuid.uuid4().hex[:12]
    store.create_application(new_id, str(random.randint(100000, 999999)), user.email,
                             {"form": form, "wizardStage": "product", "wizardIndex": 2, "company": data.get("company"),
                              "ownerName": user.name, "decision": None}, "Клиент", "Создан черновик с изменёнными параметрами")
    return fresh(new_id, user)


@app.post("/api/v1/applications/{app_id}/sign", tags=["Решение"], summary="Подписать договор (имитация)")
def sign(app_id: str, user: User = Depends(current_user)):
    row = load(app_id, user)
    data = row["data"]; data["signedAt"] = now_iso()
    if not store.transition(app_id, "accepted", "signed", "Клиент", "Договор подписан простой электронной подписью (имитация)",
                            data, time.time() + STEP_SECONDS):
        raise HTTPException(409, "Сначала примите предложение")
    return fresh(app_id, user)


class StaffDecision(BaseModel):
    kind: Literal["approved", "counteroffer", "declined"]
    amount: Optional[int] = Field(None, ge=300_000)
    months: Optional[int] = Field(None, ge=1, le=120)
    reason: Optional[str] = Field(None, max_length=500)


@app.post("/api/v1/applications/{app_id}/staff-decision", tags=["Рабочее место сотрудника"], summary="Решение по ручной проверке")
def staff_decision(app_id: str, body: StaffDecision, user: User = Depends(staff_only)):
    row = load(app_id, user)
    data = row["data"]
    q = credit.quote(data["form"])
    score = (data.get("decision") or {}).get("score")
    if body.kind == "declined":
        result = {"kind": "declined", "reason": body.reason or "После ручной проверки заявку пока нельзя одобрить."}
        label = "Отказ после ручной проверки"
    else:
        if not body.amount or not body.months:
            raise HTTPException(422, "Укажите сумму и срок")
        result = credit.offer(body.kind, q, body.amount, body.months,
                              "Специалист предложил изменить сумму или срок, чтобы платёж соответствовал выручке."
                              if body.kind == "counteroffer" else "Решение принято специалистом банка.")
        label = "Контрпредложение сотрудника" if body.kind == "counteroffer" else "Одобрено сотрудником"
    data["decision"] = {**result, "score": score, "productTitle": q.product["title"], "actor": user.name}
    if not store.transition(app_id, "manual_review", body.kind, user.name, label, data):
        raise HTTPException(409, "Заявка уже не на ручной проверке")
    return fresh(app_id, user)


@app.get("/health", tags=["Сервис"])
async def health():
    deps = {}
    for name, url in (("mocks", MOCKS_URL), ("scoring", SCORING_URL)):
        try:
            deps[name] = (await http.get(url + "/health", timeout=3)).status_code == 200
        except httpx.HTTPError:
            deps[name] = False
    return {"status": "ok" if all(deps.values()) else "degraded", "dependencies": deps}


# ---------- конвейер статусов ----------
def front_score(s: dict) -> dict:
    return {"total": s["total"], "modelUsed": s["model"]["used"], "model": s["model"], "loadRatio": s.get("load_ratio"),
            "factors": [{"name": f["name"], "value": f["value"], "max": f["max"], "note": f["note"]} for f in s["factors"]]}


async def advance(row: dict):
    app_id, status, data = row["id"], row["status"], json.loads(row["data"])
    nxt = time.time() + STEP_SECONDS
    try:
        if status == "submitted":
            store.transition(app_id, status, "checking", "Система", "Проверка полноты и корректности данных", None, nxt)
        elif status == "checking":
            public = data.get("public") or await fetch_public(data["form"]["tin"])
            reason = credit.stop_factor(public["egrul"], public["fssp"], today())
            if reason:
                data["decision"] = {"kind": "declined", "reason": reason + " Это стоп-фактор.", "productTitle": data["product"]["title"]}
                store.transition(app_id, status, "declined", "Система", "Отказ по стоп-фактору", data)
            elif public["fns"].get("statements") is None and not data.get("documentName"):
                store.transition(app_id, status, "needs_docs", "Система", "Запрошена выписка по счёту: нет отчётности в ФНС", data)
            else:
                store.transition(app_id, status, "enrichment", "Система", "Получение сведений о компании", data, nxt)
        elif status == "enrichment":
            inn = data["form"]["tin"]
            public = await fetch_public(inn)
            bki = await mock("bki", inn)  # согласие на запрос в БКИ уже в журнале
            data["enrichment"] = {**public, "bki": bki, "at": now_iso()}
            store.transition(app_id, status, "scoring", "Система", "Сведения ЕГРЮЛ, ФНС, ФССП и БКИ получены", data, nxt)
        elif status == "scoring":
            await run_scoring(app_id, data)
        elif status == "signed":
            data["issuedAt"] = now_iso()
            store.transition(app_id, status, "issued", "Система", "Деньги зачислены на счёт (имитация)", data)
    except (httpx.HTTPError, KeyError) as error:
        attempts = row.get("attempts", 0) + 1
        log.warning("step %s for %s failed (attempt %s): %s", status, app_id, attempts, error)
        store.retry_later(app_id, time.time() + min(30, 2 ** attempts), str(error))


async def run_scoring(app_id: str, data: dict):
    form, e = data["form"], data["enrichment"]
    q = credit.quote(form)
    revenue = float(form.get("monthlyRevenue") or 0)
    current = max(float(form.get("currentPayments") or 0), float(e["bki"].get("monthly_payments_rub") or 0))
    request = {
        "inn": form["tin"], "registered_on": e["egrul"]["registered_on"], "okved": e["egrul"]["okved"],
        "statements": e["fns"].get("statements"), "credit_history": e["bki"]["credit_history"],
        "tax_debt_rub": e["fns"].get("tax_debt_rub", 0), "court_cases_as_defendant": e["fssp"].get("court_cases_as_defendant", 0),
        "fssp_debt_rub": e["fssp"].get("debt_rub", 0), "monthly_revenue_rub": revenue,
        "current_payments_rub": current, "new_payment_rub": round(q.payment, 2),
    }
    r = await http.post(f"{SCORING_URL}/api/v1/score", json=request)
    r.raise_for_status()
    score = r.json()
    result = credit.decide(score["total"], q, revenue)
    data["decision"] = {**result, "score": front_score(score), "productTitle": q.product["title"]}
    labels = {"approved": "Заявка одобрена", "counteroffer": "Подготовлено контрпредложение",
              "manual_review": "Направлена на ручную проверку", "declined": "Отказано по баллам скоринга"}
    store.transition(app_id, "scoring", result["kind"], "Система", labels[result["kind"]], data)
