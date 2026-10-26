"""Authenticated cabinet gateway to the real parser and questionnaire API.

No company generation, credit-bureau substitutes or banking decision endpoints.
The parser owns questionnaire data; this database owns accounts and access links.
"""
import hashlib
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

PARSER_URL = os.getenv('PARSER_URL', 'http://fns-parser:8000').rstrip('/')
DB_PATH = os.getenv('DB_PATH', '/data/accounts.db')
ID = Annotated[str, Path(pattern=r'^[A-Za-z0-9_-]{1,128}$')]


def now():
    return datetime.now(timezone.utc)


class Accounts:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts(email TEXT PRIMARY KEY, salt TEXT, password_hash TEXT);
                CREATE TABLE IF NOT EXISTS logins(token_hash TEXT PRIMARY KEY, email TEXT, expires_at TEXT);
                CREATE TABLE IF NOT EXISTS credit_links(id TEXT PRIMARY KEY, email TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS job_links(id TEXT PRIMARY KEY, email TEXT, session_id TEXT, kind TEXT, created_at TEXT);
            ''')
            email = os.getenv('INITIAL_EMAIL', 'user@local.test').lower()
            password = os.getenv('INITIAL_PASSWORD', 'local-change-me')
            salt = secrets.token_hex(16)
            db.execute('INSERT OR IGNORE INTO accounts VALUES (?,?,?)', (email, salt, self.hash(password, salt)))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200000).hex()

    def login(self, email, password):
        with self.db() as db:
            row = db.execute('SELECT * FROM accounts WHERE email=?', (email,)).fetchone()
            if not row or not secrets.compare_digest(row['password_hash'], self.hash(password, row['salt'])):
                raise HTTPException(401, 'Неверный логин или пароль')
            token = secrets.token_urlsafe(32)
            expires = (now() + timedelta(hours=12)).isoformat()
            db.execute('INSERT INTO logins VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), email, expires))
            return {'token': token, 'expiresAt': expires, 'user': {'email': email}}

    def user(self, token):
        with self.db() as db:
            row = db.execute('SELECT email,expires_at FROM logins WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or datetime.fromisoformat(row['expires_at']) <= now():
            raise HTTPException(401, 'Войдите в кабинет заново')
        return row['email']

    def own(self, table, key, email):
        # Table names are fixed by internal callers, never read from the request.
        if table not in ('credit_links', 'job_links'):
            raise ValueError('Unknown ownership table')
        with self.db() as db:
            row = db.execute(f'SELECT * FROM {table} WHERE id=? AND email=?', (key, email)).fetchone()
        if not row:
            raise HTTPException(404, 'Объект не найден')
        return dict(row)


accounts = Accounts(DB_PATH)


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(base_url=PARSER_URL, timeout=90) as client:
        app.state.parser = client
        yield


app = FastAPI(title='Кабинет подбора финансирования', version='2.0.0', lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


def user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != 'bearer':
        raise HTTPException(401, 'Войдите в кабинет')
    return accounts.user(credentials.credentials)


async def forward(method, path, body=None, params=None):
    try:
        response = await app.state.parser.request(method, path, json=body, params=params)
    except httpx.HTTPError:
        raise HTTPException(503, 'Парсер временно недоступен. Сохранённые ответы не потеряны.')
    return response


def reply(response):
    return Response(response.content, status_code=response.status_code,
                    media_type=response.headers.get('content-type', 'application/json'),
                    headers={'Cache-Control': 'no-store', **({'Retry-After': response.headers['retry-after']} if 'retry-after' in response.headers else {})})


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


@app.post('/api/v1/auth/login')
def login(value: Login):
    return accounts.login(value.email.strip().lower(), value.password)


@app.get('/api/v1/auth/me')
def me(email=Depends(user)):
    return {'email': email}


@app.post('/api/v1/auth/logout', status_code=204)
def logout(authorization: str = Header(), email=Depends(user)):
    with accounts.db() as db:
        db.execute('DELETE FROM logins WHERE token_hash=?', (hashlib.sha256(authorization[7:].encode()).hexdigest(),))
    return Response(status_code=204)


@app.get('/health')
async def health():
    response = await forward('GET', '/health')
    return reply(response)


@app.get('/api/v1/credit/sessions')
def listing(email=Depends(user)):
    with accounts.db() as db:
        return [dict(row) for row in db.execute('SELECT id,created_at FROM credit_links WHERE email=? ORDER BY created_at DESC', (email,))]


@app.post('/api/v1/credit/sessions', status_code=201)
async def create(email=Depends(user)):
    response = await forward('POST', '/api/v1/credit/sessions', {'flow_version': 2})
    if response.is_success:
        with accounts.db() as db:
            db.execute('INSERT INTO credit_links VALUES (?,?,?)', (response.json()['session_id'], email, now().isoformat()))
    return reply(response)


@app.get('/api/v1/credit/sessions/{sid}')
async def get(sid: ID, email=Depends(user)):
    accounts.own('credit_links', sid, email)
    return reply(await forward('GET', f'/api/v1/credit/sessions/{sid}'))


@app.get('/api/v1/credit/sessions/{sid}/result')
async def result(sid: ID, email=Depends(user)):
    accounts.own('credit_links', sid, email)
    return reply(await forward('GET', f'/api/v1/credit/sessions/{sid}/result'))


@app.post('/api/v1/credit/sessions/{sid}/answers')
async def answer(sid: ID, value: dict = Body(..., examples=[{"revision": 0, "question_id": "knowledge", "value": "no"}]), email=Depends(user)):
    accounts.own('credit_links', sid, email)
    return reply(await forward('POST', f'/api/v1/credit/sessions/{sid}/answers', value))


@app.post('/api/v1/credit/sessions/{sid}/answers/batch')
async def answers(sid: ID, value: dict = Body(..., examples=[{'revision': 0, 'answers': [
        {'question_id': 'asset_cost', 'value': 5000000}, {'question_id': 'amount', 'value': 3000000}]}]), email=Depends(user)):
    accounts.own('credit_links', sid, email)
    return reply(await forward('POST', f'/api/v1/credit/sessions/{sid}/answers/batch', value))


@app.post('/api/v1/credit/sessions/{sid}/registry')
async def attach(sid: ID, value: dict = Body(..., examples=[{"revision": 1, "job_id": "job-id", "selected_ogrn": "1124028002273"}]), email=Depends(user)):
    accounts.own('credit_links', sid, email)
    if not isinstance(value.get('job_id'), str):
        raise HTTPException(422, 'Нужен job_id')
    accounts.own('job_links', value['job_id'], email)
    return reply(await forward('POST', f'/api/v1/credit/sessions/{sid}/registry', value))


@app.delete('/api/v1/credit/sessions/{sid}')
async def delete(sid: ID, request: Request, email=Depends(user)):
    accounts.own('credit_links', sid, email)
    response = await forward('DELETE', f'/api/v1/credit/sessions/{sid}', params=request.query_params)
    if response.is_success:
        with accounts.db() as db:
            db.execute('DELETE FROM credit_links WHERE id=?', (sid,))
    return reply(response)


async def start_job(sid, email, kind, value):
    accounts.own('credit_links', sid, email)
    path = '/api/v1/jobs' if kind == 'registry' else f'/api/v1/credit/sessions/{sid}/scoring/jobs'
    response = await forward('POST', path, value)
    if response.is_success:
        with accounts.db() as db:
            db.execute('INSERT INTO job_links VALUES (?,?,?,?,?)', (response.json()['job_id'], email, sid, kind, now().isoformat()))
    return reply(response)


@app.post('/api/v1/credit/sessions/{sid}/registry/jobs')
async def registry_job(sid: ID, value: dict = Body(..., examples=[{"identifier": "5505223431", "full": False, "enrich": True}]), email=Depends(user)):
    return await start_job(sid, email, 'registry', value)


@app.post('/api/v1/credit/sessions/{sid}/scoring/jobs')
async def scoring_job(sid: ID, value: dict = Body(..., examples=[{"revision": 1}]), email=Depends(user)):
    return await start_job(sid, email, 'scoring', value)


@app.get('/api/v1/credit/sessions/{sid}/jobs')
def session_jobs(sid: ID, email=Depends(user)):
    accounts.own('credit_links', sid, email)
    with accounts.db() as db:
        return [dict(row) for row in db.execute('SELECT id,kind,created_at FROM job_links WHERE session_id=? AND email=? ORDER BY created_at DESC', (sid,email))]


@app.get('/api/v1/jobs/{jid}')
async def job(jid: ID, email=Depends(user)):
    accounts.own('job_links', jid, email)
    return reply(await forward('GET', f'/api/v1/jobs/{jid}'))


@app.delete('/api/v1/jobs/{jid}')
async def cancel(jid: ID, email=Depends(user)):
    accounts.own('job_links', jid, email)
    return reply(await forward('DELETE', f'/api/v1/jobs/{jid}'))


@app.get('/api/v1/jobs/{jid}/captchas/{cid}/image')
async def image(jid: ID, cid: ID, email=Depends(user)):
    accounts.own('job_links', jid, email)
    return reply(await forward('GET', f'/api/v1/jobs/{jid}/captchas/{cid}/image'))


@app.post('/api/v1/jobs/{jid}/captchas/{cid}/answer')
async def captcha(jid: ID, cid: ID, value: dict = Body(..., examples=[{"action": "answer", "code": "123456"}]), email=Depends(user)):
    accounts.own('job_links', jid, email)
    return reply(await forward('POST', f'/api/v1/jobs/{jid}/captchas/{cid}/answer', value))


@app.get('/api/v1/scoring/model')
async def model(email=Depends(user)):
    return reply(await forward('GET', '/api/v1/scoring/model'))
