"""Bounded in-memory jobs with human captcha handoff and expiring capability IDs."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import secrets
from threading import Event, RLock, Thread
import time

from .manual_captcha import CaptchaImage
from .transport import SourceError

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


def stamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat()


class JobError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Challenge:
    id: str
    image: CaptchaImage
    source: str
    inn: str
    source_url: str
    attempt: int
    deadline: float
    expires_at: str
    state: str = "pending"
    action: tuple[str, str | None] | None = None
    event: Event = field(default_factory=Event)


@dataclass
class Job:
    id: str
    identifier: str
    state: str = "running"
    created_at: str = field(default_factory=stamp)
    finished_at: str | None = None
    finished_clock: float | None = None
    result: dict | None = None
    error: dict | None = None
    challenge: Challenge | None = None
    cancelled: Event = field(default_factory=Event)


class JobStore:
    def __init__(self, slots, *, captcha_timeout=120, retention=900, max_jobs=32):
        self.slots = slots
        self.captcha_timeout, self.retention, self.max_jobs = captcha_timeout, retention, max_jobs
        self.jobs = {}
        self.lock = RLock()
        self.closed = False

    def _clean(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.finished_clock is not None and now - job.finished_clock >= self.retention:
                del self.jobs[key]

    def _get(self, job_id):
        self._clean()
        job = self.jobs.get(job_id)
        if job is None:
            raise JobError(404, "job_not_found", "Задание не найдено или срок хранения истёк")
        return job

    def start(self, identifier, runner):
        with self.lock:
            self._clean()
            if self.closed or len(self.jobs) >= self.max_jobs or not self.slots.acquire(blocking=False):
                raise JobError(503, "busy", "Нет свободного места для задания. Повторите позже.")
            job = Job(secrets.token_urlsafe(24), identifier)
            self.jobs[job.id] = job
            try:
                Thread(target=self._run, args=(job, runner), daemon=True, name="manual-registry-job").start()
            except Exception:
                del self.jobs[job.id]
                self.slots.release()
                raise
            return self._snapshot(job)

    def _run(self, job, runner):
        try:
            result = runner(lambda **kwargs: self.ask(job, **kwargs))
            with self.lock:
                if not job.cancelled.is_set():
                    job.result, job.state = result, "completed"
        except Exception:
            logger.exception("Ошибка фонового задания парсера")
            with self.lock:
                if not job.cancelled.is_set():
                    job.state = "failed"
                    job.error = {"code": "internal_error", "message": "Не удалось завершить задание"}
        finally:
            with self.lock:
                job.challenge = None  # Do not retain images or submitted answers.
                job.finished_at, job.finished_clock = stamp(), time.monotonic()
            self.slots.release()

    def ask(self, job, *, image, source, inn, source_url, attempt):
        with self.lock:
            if self.closed or job.cancelled.is_set():
                raise SourceError("captcha_skipped", "Задание отменено")
            challenge = Challenge(secrets.token_urlsafe(18), image, source, inn, source_url, attempt,
                                  time.monotonic() + self.captcha_timeout, stamp(time.time() + self.captcha_timeout))
            job.challenge, job.state = challenge, "waiting_for_captcha"
        challenge.event.wait(self.captcha_timeout)
        with self.lock:
            job.challenge = None
            if job.cancelled.is_set() or self.closed:
                raise SourceError("captcha_skipped", "Задание отменено")
            job.state = "running"
            if challenge.action is None:
                raise SourceError("captcha_timeout", "Истекло время ожидания ручного ответа на капчу")
            return challenge.action

    def _challenge(self, job_id, challenge_id):
        job = self._get(job_id)
        challenge = job.challenge
        if challenge is None or challenge.id != challenge_id or challenge.state != "pending":
            raise JobError(409, "challenge_not_pending", "Капча уже обработана или заменена")
        if challenge.deadline <= time.monotonic():
            challenge.event.set()
            raise JobError(410, "challenge_expired", "Время ожидания ответа истекло")
        if job.cancelled.is_set():
            raise JobError(409, "job_cancelled", "Задание отменено")
        return job, challenge

    def answer(self, job_id, challenge_id, action, code=None):
        with self.lock:
            job, challenge = self._challenge(job_id, challenge_id)
            if challenge.source in ('egrul', 'transparent_business') and action == 'answer' and (
                    not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit()):
                raise JobError(422, 'invalid_captcha_answer', 'Введите 6 цифр с картинки ФНС')
            challenge.action = (action, code)
            challenge.state, job.state = "submitted", "running"
            challenge.event.set()
            return {"status": "accepted"}

    def image(self, job_id, challenge_id):
        with self.lock:
            _, challenge = self._challenge(job_id, challenge_id)
            return challenge.image

    def cancel(self, job_id):
        with self.lock:
            job = self._get(job_id)
            if job.state not in TERMINAL:
                job.state = "cancelled"
                job.cancelled.set()
                if job.challenge:
                    job.challenge.event.set()
            return self._snapshot(job)

    def _snapshot(self, job):
        result = {"job_id": job.id, "identifier": job.identifier, "state": job.state,
                  "created_at": job.created_at, "status_url": f"/api/v1/jobs/{job.id}",
                  "page_url": f"/captcha#job={job.id}"}
        if job.finished_at:
            result["finished_at"] = job.finished_at
        if job.error:
            result["error"] = job.error
        if job.result is not None:
            result["result"] = job.result
        challenge = job.challenge
        if challenge and challenge.state == "pending" and not job.cancelled.is_set():
            prefix = f"/api/v1/jobs/{job.id}/captchas/{challenge.id}"
            result["captcha"] = {"id": challenge.id, "source": challenge.source,
                                 "inn": challenge.inn, "source_url": challenge.source_url,
                                 "attempt": challenge.attempt, "expires_at": challenge.expires_at,
                                 "image_url": prefix + "/image", "answer_url": prefix + "/answer"}
        return deepcopy(result)

    def get(self, job_id):
        with self.lock:
            return self._snapshot(self._get(job_id))

    def close(self):
        with self.lock:
            self.closed = True
            for job in list(self.jobs.values()):
                if job.state not in TERMINAL:
                    self.cancel(job.id)
