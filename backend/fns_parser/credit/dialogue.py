"""Replay a validated prefix: branch changes cannot retain stale downstream answers."""

from dataclasses import dataclass
from datetime import date

from .flow import questions
from .questions import CreditError, Question


@dataclass
class Progress:
    answers: dict
    answered: list[Question]
    next_question: Question | None


def replay(entries: list[dict], today: date, version: int = 1) -> Progress:
    flow = questions(today, version)
    question = next(flow)
    answers = {"today": today}
    answered = []
    for entry in entries:
        if question is None or entry["id"] != question.id:
            raise CreditError(409, "invalid_dialogue", "Ответ не соответствует текущей ветке анкеты")
        value = question.validate_answer(entry["value"], answers)
        answers[question.id] = value
        answered.append(question)
        try:
            question = flow.send(value)
        except StopIteration as done:
            answers, question = done.value, None
    # The generator also derives these values before returning a complete record.
    if answers.get("regular") == "yes":
        answers["reuse"] = "yes"
    if answers.get("own_funds") == "no":
        answers["own_amount"] = 0
    return Progress(answers, answered, question)


def submit(record: dict, field_id: str, value):
    today = date.fromisoformat(record["date"])
    version = record.get('flow_version', 1)
    progress = replay(record["entries"], today, version)
    active = progress.answered + ([progress.next_question] if progress.next_question else [])
    index = next((i for i, q in enumerate(active) if q.id == field_id), None)
    if index is None:
        raise CreditError(409, "unexpected_question", "Можно ответить на текущий вопрос или исправить уже заданный")
    entries = record["entries"][:index] + [{"id": field_id, "value": value}]
    changed = replay(entries, today, version)  # Validation happens before any persisted mutation.
    normalized = changed.answers[field_id]
    entries[-1]["value"] = normalized.isoformat() if isinstance(normalized, date) else normalized
    if index < len(record["entries"]) and entries[-1] == record["entries"][index]:
        return  # Same answer is a no-op; retain the rest of the dialogue.
    record["entries"] = entries
    record['scoring'], record['scoring_job_id'] = None, None
    if changed.answers.get("inn") != progress.answers.get("inn"):
        record["registry"], record["selected_ogrn"] = None, None
