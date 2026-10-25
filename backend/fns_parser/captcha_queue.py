"""SQLite handoff between the batch process and FastAPI on the same server.

FNS cookies/tokens stay in the collector. Only verified images and human
answers cross this queue. Answers are consumed once and immediately erased.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .fns_captcha import SOURCES, raster_image
from .jobs import JobError
from .transport import SourceError


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def default_db():
    return Path(os.environ.get("FNS_CAPTCHA_DB", "data/captcha.sqlite3"))


class CaptchaQueue:
    def __init__(self, path=None):
        self.path = Path(path if path is not None else default_db()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS challenges (
                id TEXT PRIMARY KEY, worker TEXT NOT NULL, pid INTEGER NOT NULL,
                identifier TEXT NOT NULL, source TEXT NOT NULL, attempt INTEGER NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                heartbeat REAL NOT NULL, image BLOB, media_type TEXT,
                action TEXT, answer TEXT)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def clean(self, db):
        now = time.time()
        db.execute("UPDATE challenges SET state='expired',image=NULL,answer=NULL WHERE state IN ('pending','answered') AND expires<=?", (now,))
        db.execute("UPDATE challenges SET state='abandoned',image=NULL,answer=NULL WHERE state IN ('pending','answered') AND heartbeat<?", (now - 60,))
        db.execute("DELETE FROM challenges WHERE state NOT IN ('pending','answered') AND created<?", (now - 86400,))

    def publish(self, worker, *, image, source, inn, source_url, attempt, timeout):
        if source not in SOURCES or source_url != SOURCES[source]:
            raise ValueError("Unsupported captcha source")
        picture = raster_image(image.content)
        identifier = str(inn)
        if not identifier.isascii() or not identifier.isdigit() or len(identifier) not in (10, 12, 13, 15):
            raise ValueError("Invalid company identifier")
        key, now = secrets.token_urlsafe(24), time.time()
        with self.connect() as db:
            self.clean(db)
            db.execute("INSERT INTO challenges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key, worker, os.getpid(), identifier, source, attempt, 'pending', now, now + timeout, now,
                        picture.content, picture.media_type, None, None))
        return key

    @staticmethod
    def view(row):
        prefix = '/api/v1/batch/captchas/' + row['id']
        return {"id": row['id'], "source": row['source'], "inn": row['identifier'],
                "source_url": SOURCES[row['source']], "attempt": row['attempt'],
                "state": row['state'], "created_at": stamp(row['created']),
                "expires_at": stamp(row['expires']), "heartbeat_at": stamp(row['heartbeat']),
                "worker_pid": row['pid'], "image_url": prefix + '/image', "answer_url": prefix + '/answer'}

    def pending(self):
        with self.connect() as db:
            self.clean(db)
            return [self.view(row) for row in db.execute("SELECT * FROM challenges WHERE state='pending' ORDER BY created LIMIT 32")]

    def _pending(self, db, key):
        self.clean(db)
        row = db.execute("SELECT * FROM challenges WHERE id=?", (key,)).fetchone()
        if row is None:
            raise JobError(404, "challenge_not_found", "Капча не найдена")
        if row['state'] != 'pending':
            raise JobError(409, "challenge_not_pending", "Капча уже обработана или устарела")
        return row

    def image(self, key):
        with self.connect() as db:
            row = self._pending(db, key)
            return row['image'], row['media_type']

    def answer(self, key, action, code=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._pending(db, key)
            if action not in ('answer','refresh','skip') or (action == 'answer' and
                    (not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit())):
                raise JobError(422, "invalid_captcha_answer", "Введите 6 цифр с картинки ФНС")
            db.execute("UPDATE challenges SET state='answered',action=?,answer=?,image=NULL WHERE id=?",
                       (action, code if action == 'answer' else None, key))
        return {"status": "accepted"}

    def consume(self, key, worker):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.clean(db)
            row = db.execute("SELECT * FROM challenges WHERE id=? AND worker=?", (key, worker)).fetchone()
            if row is None or row['state'] not in ('pending','answered'):
                raise SourceError("captcha_timeout", "Ожидание ручного ввода истекло; результаты сохранены")
            db.execute("UPDATE challenges SET heartbeat=? WHERE id=?", (time.time(), key))
            if row['state'] == 'answered':
                db.execute("UPDATE challenges SET state='consumed',answer=NULL,image=NULL WHERE id=?", (key,))
                return row['action'], row['answer']
        return None

    def abandon(self, key, worker):
        with self.connect() as db:
            db.execute("UPDATE challenges SET state='abandoned',image=NULL,answer=NULL WHERE id=? AND worker=? AND state IN ('pending','answered')", (key, worker))


class QueueCaptchaHandler:
    def __init__(self, queue, *, timeout=1800, progress=None, interval=1):
        self.queue, self.timeout, self.progress, self.interval = queue, timeout, progress, interval
        self.worker = secrets.token_urlsafe(24)

    def __call__(self, **challenge):
        key = self.queue.publish(self.worker, timeout=self.timeout, **challenge)
        print(f"CAPTCHA {challenge['source']} {challenge['inn']}: /captcha/batch", flush=True)
        try:
            while True:
                if self.progress:
                    self.progress(state='waiting_for_captcha', captcha_id=key, source=challenge['source'])
                action = self.queue.consume(key, self.worker)
                if action is not None:
                    return action
                time.sleep(self.interval)
        finally:
            self.queue.abandon(key, self.worker)
            if self.progress:
                self.progress(state='running', captcha_id=None, source=None)
