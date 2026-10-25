"""SQLite records with atomic revision checks; connections never cross threads."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3

from .questions import CreditError

MOSCOW = timezone(timedelta(hours=3))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS credit_sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def load(conn, session_id):
        row = conn.execute("SELECT payload FROM credit_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise CreditError(404, "session_not_found", "Сессия не найдена")
        return json.loads(row[0])

    @staticmethod
    def dump(record):
        return json.dumps(record, ensure_ascii=False, allow_nan=False)

    def create(self):
        now = timestamp()
        record = {"id": secrets.token_urlsafe(32), "revision": 0, "created_at": now, "updated_at": now,
                  "date": datetime.now(MOSCOW).date().isoformat(), "entries": [],
                  "registry": None, "selected_ogrn": None}
        with self.connection() as conn:
            conn.execute("INSERT INTO credit_sessions (id, payload) VALUES (?, ?)", (record["id"], self.dump(record)))
        return record

    def get(self, session_id):
        with self.connection() as conn:
            return self.load(conn, session_id)

    def update(self, session_id, revision, mutate):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self.load(conn, session_id)
            if record["revision"] != revision:
                raise CreditError(409, "revision_conflict", "Сессия уже изменена: получите свежий JSON и повторите действие")
            mutate(record)
            record["revision"] += 1
            record["updated_at"] = timestamp()
            conn.execute("UPDATE credit_sessions SET payload = ? WHERE id = ?", (self.dump(record), session_id))
        return record

    def delete(self, session_id, revision):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self.load(conn, session_id)
            if record["revision"] != revision:
                raise CreditError(409, "revision_conflict", "Сессия уже изменена: получите актуальную версию")
            conn.execute("DELETE FROM credit_sessions WHERE id = ?", (session_id,))
