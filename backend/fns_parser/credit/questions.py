"""Typed questions and validation shared by the HTTP API and dialogue replay."""

from datetime import date
import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..identifiers import validate_identifier
from .rules import BUSINESS_FORMS


class CreditError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class Option(BaseModel):
    value: str
    label: str


class Question(BaseModel):
    id: str = Field(description="Уникальное поле ответа, например loan_balance")
    question_id: str = Field(description="Номер в дереве; может повторяться у нескольких полей")
    text: str
    type: Literal["choice", "number", "integer", "text", "date"]
    options: list[Option] = Field(default_factory=list)
    allow_unknown: bool = True
    minimum: int | float | None = None
    maximum: int | float | None = None
    max_length: int | None = None

    def validate_answer(self, value: Any, previous: dict):
        def invalid(message):
            raise CreditError(422, "invalid_answer", f"{self.id}: {message}")

        if value is None:
            if not self.allow_unknown:
                invalid("выберите один из доступных ответов")
            return "unknown" if self.type == "choice" else None
        if self.type == "choice":
            if not isinstance(value, str) or value not in {o.value for o in self.options}:
                invalid("недопустимый вариант ответа")
            if self.id == "form" and ((previous.get("registered") == "yes") != (value in BUSINESS_FORMS)):
                invalid("форма должна соответствовать текущему статусу регистрации Q11")
        elif self.type in {"number", "integer"}:
            if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
                invalid("ожидается конечное JSON-число или null")
            if self.type == "integer" and value != int(value):
                invalid("ожидается целое число")
            if self.minimum is not None and value < self.minimum:
                invalid(f"минимум {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                invalid(f"максимум {self.maximum}")
            value = int(value) if value == int(value) else value
        elif self.type == "date":
            try:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value or parsed.year > 9998:
                    raise ValueError
            except ValueError:
                invalid("дата должна быть в формате YYYY-MM-DD (год 0001–9998)")
            value = parsed
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > (self.max_length or 2000):
                invalid("ожидается непустой текст до 2000 символов или null")
            value = value.strip()
            if self.id == "inn":
                try:
                    value, _ = validate_identifier(value)
                    expected = 12 if previous.get("form") == "ip" else 10
                    if len(value) != expected:
                        raise ValueError(f"Для выбранной формы нужен ИНН из {expected} цифр")
                except ValueError as exc:
                    invalid(str(exc))
        return value


def choice(key, qid, prompt, options):
    return Question(id=key, question_id=qid, text=prompt, type="choice",
                    options=[Option(value=k, label=v) for k, v in options],
                    allow_unknown=any(k == "unknown" for k, _ in options))


def yn(key, qid, prompt, unknown=True):
    return choice(key, qid, prompt, [("yes", "Да"), ("no", "Нет")]
                  + ([("unknown", "Не знаю")] if unknown else []))


def number(key, qid, prompt, *, minimum=0, integer=False, negative=False):
    return Question(id=key, question_id=qid, text=prompt, type="integer" if integer else "number",
                    minimum=-10**15 if negative else minimum, maximum=10**9 if integer else 10**15)


def text(key, qid, prompt):
    return Question(id=key, question_id=qid, text=prompt, type="text", max_length=2000)


def day(key, qid, prompt):
    return Question(id=key, question_id=qid, text=prompt, type="date")
