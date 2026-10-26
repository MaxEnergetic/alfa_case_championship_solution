"""SQLite-хранилище: пользователи, сессии, заявки, история статусов, согласия, документы."""
import hashlib
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('client','staff')),
  password_salt TEXT NOT NULL, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, email TEXT NOT NULL REFERENCES users(email), expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY, display_id TEXT NOT NULL, owner_email TEXT NOT NULL REFERENCES users(email),
  status TEXT NOT NULL, data TEXT NOT NULL, next_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_applications_owner ON applications(owner_email);
CREATE INDEX IF NOT EXISTS ix_applications_due ON applications(next_at);
CREATE TABLE IF NOT EXISTS status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL REFERENCES applications(id),
  from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, label TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL REFERENCES applications(id),
  type TEXT NOT NULL, text TEXT NOT NULL, user_email TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id), filename TEXT NOT NULL,
  content_type TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, uploaded_at TEXT NOT NULL
);
"""

DEMO_USERS = [
    ("client@demo.alfa.ru", "Алексей Смирнов", "client"),
    ("manager@demo.alfa.ru", "Мария Волкова", "staff"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


class Store:
    def __init__(self, path: str, demo_password: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        for email, name, role in DEMO_USERS:
            if not self.db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                salt = secrets.token_hex(16)
                self.db.execute("INSERT INTO users VALUES (?,?,?,?,?)", (email, name, role, salt, hash_password(demo_password, salt)))

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    # --- пользователи и сессии ---
    def check_password(self, email: str, password: str):
        row = self.db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row and secrets.compare_digest(row["password_hash"], hash_password(password, row["password_salt"])):
            return dict(row)
        return None

    def create_session(self, email: str, expires_at: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.tx() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), email, expires_at))
        return token

    def session_user(self, token: str):
        row = self.db.execute(
            "SELECT u.email, u.name, u.role, s.expires_at FROM sessions s JOIN users u ON u.email=s.email WHERE s.token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row["expires_at"] < now_iso():
            return None
        return dict(row)

    def drop_session(self, token: str):
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    # --- заявки ---
    def create_application(self, app_id: str, display_id: str, owner: str, data: dict, actor: str, label: str) -> None:
        ts = now_iso()
        with self.tx() as db:
            db.execute("INSERT INTO applications (id, display_id, owner_email, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                       (app_id, display_id, owner, "draft", json.dumps(data, ensure_ascii=False), ts, ts))
            db.execute("INSERT INTO status_history (application_id, from_status, to_status, actor, label, at) VALUES (?,?,?,?,?,?)",
                       (app_id, None, "draft", actor, label, ts))

    def get(self, app_id: str):
        row = self.db.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return dict(row) if row else None

    def list_apps(self, owner: str | None):
        if owner:
            rows = self.db.execute("SELECT * FROM applications WHERE owner_email=? ORDER BY created_at DESC", (owner,)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM applications ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def due(self, now: float):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM applications WHERE next_at IS NOT NULL AND next_at <= ? ORDER BY next_at", (now,)).fetchall()]

    def save_data(self, app_id: str, data: dict) -> None:
        with self.tx() as db:
            db.execute("UPDATE applications SET data=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), now_iso(), app_id))

    def transition(self, app_id: str, expected: str | tuple, status: str, actor: str, label: str,
                   data: dict | None = None, next_at: float | None = None) -> bool:
        """Сменить статус атомарно: только если текущий статус совпадает с ожидаемым (защита от гонок)."""
        expected = (expected,) if isinstance(expected, str) else expected
        ts = now_iso()
        with self.tx() as db:
            row = db.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()
            if not row or row["status"] not in expected:
                return False
            sets, args = ["status=?", "updated_at=?", "next_at=?", "attempts=0"], [status, ts, next_at]
            if data is not None:
                sets.append("data=?"); args.append(json.dumps(data, ensure_ascii=False))
            db.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id=?", (*args, app_id))
            db.execute("INSERT INTO status_history (application_id, from_status, to_status, actor, label, at) VALUES (?,?,?,?,?,?)",
                       (app_id, row["status"], status, actor, label, ts))
        return True

    def retry_later(self, app_id: str, next_at: float, error: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE applications SET next_at=?, attempts=attempts+1, updated_at=? WHERE id=?", (next_at, now_iso(), app_id))

    def history(self, app_id: str):
        return [dict(r) for r in self.db.execute(
            "SELECT from_status, to_status, actor, label, at FROM status_history WHERE application_id=? ORDER BY id", (app_id,)).fetchall()]

    def add_consents(self, app_id: str, items: list[tuple[str, str]], user: str) -> None:
        ts = now_iso()
        with self.tx() as db:
            for kind, text in items:
                db.execute("INSERT INTO consents (application_id, type, text, user_email, at) VALUES (?,?,?,?,?)", (app_id, kind, text, user, ts))

    def consents(self, app_id: str):
        return [dict(r) for r in self.db.execute(
            "SELECT type, text, user_email, at FROM consents WHERE application_id=? ORDER BY id", (app_id,)).fetchall()]

    def add_document(self, doc: dict) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO documents VALUES (:id,:application_id,:filename,:content_type,:size,:sha256,:path,:uploaded_at)", doc)

    def documents(self, app_id: str):
        return [dict(r) for r in self.db.execute(
            "SELECT id, filename, content_type, size, sha256, uploaded_at FROM documents WHERE application_id=? ORDER BY uploaded_at", (app_id,)).fetchall()]
