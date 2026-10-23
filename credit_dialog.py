#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Терминальный подбор типа финансирования по карте Альфахака.

Python 3.10+, только стандартная библиотека. Запуск: python credit_dialog.py
Примеры без ввода: python credit_dialog.py --demo line
Каталог: python credit_dialog.py --products

Основа: согласованная карта от 23.10.2026, после удаления Q14-Q16 и Q38-Q41.
Это подбор типа продукта, а не банковский скоринг. Скрипт не обращается к API,
не проверяет ИНН в реестрах и ничего не сохраняет. Условия - снимок карты,
не автоматически обновляемые тарифы банка. Неподтверждённые условия P11,
личных программ и части залоговых продуктов не считаются подтверждёнными.

collect_answers() отвечает за диалог; recommend() - за чистую логику.
В PRODUCTS / RULES вынесены названия и подтверждённые рамки из карты.
Сведения возраста и региона бизнеса остаются проверками этапа заявки:
удалённые вопросы не задаются под другими номерами.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Any

RULES_AS_OF = "23.10.2026"
PRODUCTS = {
    "P01": "Кредит одной суммой без залога",
    "P02": "Овердрафт",
    "P03": "Возобновляемая кредитная линия без залога",
    "P04": "Кредит одной суммой с залогом",
    "P05": "Возобновляемая кредитная линия с залогом",
    "P06": "Невозобновляемая кредитная линия с залогом",
    "P07": "Рефинансирование бизнеса",
    "P08": "Бизнес-ипотека",
    "P09": "Автокредит для бизнеса",
    "P10": "Деньги на дело",
    "P11": "Быстрый бизнес-кредит (условия требуют подтверждения)",
    "P12": "Кредит наличными физическому лицу (отдельная программа)",
    "P13": "Рефинансирование для самозанятых (отдельная программа)",
    "P14": "Кредит наличными самозанятому (отдельная программа)",
    "A01": "Лизинг",
    "A02": "Банковская гарантия",
    "A03": "Факторинг",
}
# Только рамки, зафиксированные в согласованной карте. Отсутствие порога
# означает «нужно проверить», а не отсутствие ограничений у банка.
RULES = {
    "P01": {"min": 300_000, "max": 30_000_000, "months": (12, 60)},
    "P03": {"min": 300_000, "max": 30_000_000},
    "P04": {"min": 500_000, "max": 150_000_000, "months": (12, 120)},
}
SECURED = {"P04", "P05", "P06", "P10"}
LEGAL_FORMS = {"ooo", "other_legal"}
BUSINESS_FORMS = LEGAL_FORMS | {"ip"}
PERSONAL = {"P12", "P13", "P14"}
SOURCES = (
    "https://alfabank.ru/sme/profits/businesscredit/",
    "https://alfabank.ru/sme/profits/creditline/adv/",
    "https://alfabank.ru/sme/profits/overdraft/",
)


class Cancelled(Exception):
    """Пользователь завершил диалог или закрыл ввод."""


class Terminal:
    def __init__(self, reader: Callable[[str], str] = input,
                 writer: Callable[[str], None] = print):
        self.reader, self.writer = reader, writer
        self.asked: list[str] = []

    def raw(self, prompt: str) -> str:
        try:
            value = self.reader(prompt).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise Cancelled from exc
        if value.casefold() in {"выход", "exit", "quit", "q"}:
            raise Cancelled
        return value

    def choice(self, qid: str, prompt: str, options: list[tuple[str, str]]) -> str:
        self.asked.append(qid)
        self.writer(f"\n{qid}. {prompt}")
        for i, (_, label) in enumerate(options, 1):
            self.writer(f"  {i}. {label}")
        while True:
            value = self.raw("Ваш ответ (номер): ")
            if value.isdecimal() and 1 <= int(value) <= len(options):
                return options[int(value) - 1][0]
            self.writer(f"Введите номер от 1 до {len(options)} или 'выход'.")

    def yn(self, qid: str, prompt: str, unknown: bool = True) -> str:
        options = [("yes", "Да"), ("no", "Нет")]
        if unknown:
            options.append(("unknown", "Не знаю"))
        return self.choice(qid, prompt, options)

    def number(self, qid: str, prompt: str, *, minimum: float = 0,
               integer: bool = False, negative: bool = False) -> float | int | None:
        self.asked.append(qid)
        self.writer(f"\n{qid}. {prompt}")
        while True:
            value = self.raw("Число или '?' (не знаю): ")
            if value in {"", "?"} or value.casefold() == "не знаю":
                return None
            try:
                n = Decimal(value.replace(" ", "").replace("\u00a0", "").replace(",", "."))
                if not n.is_finite() or (not negative and n < Decimal(str(minimum))):
                    raise InvalidOperation
                if integer and n != n.to_integral_value():
                    raise InvalidOperation
                result = int(n) if integer else float(n)
                if not integer and (result == float("inf") or result == float("-inf")):
                    raise InvalidOperation
                return result
            except (InvalidOperation, ValueError, OverflowError):
                self.writer("Нужное значение: " + ("целое число" if integer else "число")
                            + (f" не меньше {minimum}" if not negative else "") + ".")

    def text(self, qid: str, prompt: str) -> str | None:
        self.asked.append(qid)
        self.writer(f"\n{qid}. {prompt}")
        value = self.raw("Ответ или '?' (не знаю): ")
        return None if value in {"", "?"} else value

    def day(self, qid: str, prompt: str) -> date | None:
        self.asked.append(qid)
        self.writer(f"\n{qid}. {prompt}")
        while True:
            value = self.raw("ДД.ММ.ГГГГ или '?' (не знаю): ")
            if value in {"", "?"}:
                return None
            try:
                d, m, y = map(int, value.split("."))
                if not 1 <= y <= 9998:
                    raise ValueError
                return date(y, m, d)
            except (ValueError, OverflowError):
                self.writer("Введите существующую дату, например 25.10.2026.")


def add_months(start: date, months: int) -> date:
    """Календарные месяцы, а не приближение «6 месяцев = 180 дней»."""
    year, month0 = divmod(start.year * 12 + start.month - 1 + months, 12)
    month = month0 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def collect_answers(ui: Terminal) -> dict[str, Any]:
    a: dict[str, Any] = {"today": date.today()}
    a["knowledge"] = ui.choice("Q01", "Вы уже знаете, какой кредит хотите?", [
        ("yes", "Да, знаю"), ("no", "Нет, помогите выбрать"),
        ("compare", "Есть вариант, хочу сравнить")])
    if a["knowledge"] != "no":
        a["preferred"] = ui.choice("Q02", "Какой продукт рассматриваете?",
                                   list(PRODUCTS.items()) + [("unknown", "Другое / не уверен")])
    goals = [("stock", "Купить товар или сырьё"), ("gap", "Оплатить расходы до выручки"),
             ("investment", "Оборудование, ремонт или расширение"),
             ("property", "Купить помещение"), ("vehicle", "Транспорт или спецтехника"),
             ("refinance", "Заменить действующий кредит"), ("startup", "Запустить новый бизнес"),
             ("contract", "Обеспечить контракт / тендер"), ("other", "Другая цель / не определился")]
    a["goal"] = ui.choice("Q03", "На что нужны деньги? Выберите основную цель.", goals)
    if a["goal"] == "other":
        a["description"] = ui.text("Q10", "Опишите потребность. Свободный текст не классифицируется автоматически.")
    if a["goal"] == "stock":
        a["regular"] = ui.choice("Q04", "Закупка разовая или регулярная?", [
            ("no", "Разовая"), ("yes", "Регулярная"), ("unknown", "Не знаю")])
        if a["regular"] == "yes":
            a["reuse"] = "yes"  # Q24 намеренно пропускается.
    if a["goal"] == "contract":
        a["contract_need"] = ui.choice("Q10b", "Нужны деньги или обеспечение обязательства?", [
            ("money", "Деньги на исполнение"), ("guarantee", "Гарантия"), ("both", "И деньги, и гарантия")])
        a["contract_details"] = ui.text("Q10b", "Укажите обязательство, сумму и срок гарантии / контракта.")
    if a["goal"] == "gap" or (a["goal"] == "contract" and a["contract_need"] != "guarantee"):
        a["gap_days"] = ui.number("Q05", "Через сколько дней после выдачи поступят деньги для возврата?",
                                   minimum=1, integer=True)
        a["receivable"] = ui.yn("Q05a", "Оплата ожидается за уже поставленный товар или оказанную услугу?")
    if a["goal"] == "investment":
        a["disbursement"] = ui.choice("Q06", "Вся сумма нужна сразу или по этапам?", [
            ("once", "Сразу"), ("parts", "По этапам"), ("unknown", "Не знаю")])
        a["asset"] = ui.choice("Q06a", "Что именно покупаете?", [
            ("equipment", "Производственное оборудование"), ("vehicle", "Транспорт / спецтехника"),
            ("other", "Другой объект / ремонт без покупки объекта"), ("unknown", "Не знаю")])
    if a["goal"] == "property":
        a["property_chosen"] = ui.yn("Q07", "Помещение уже выбрано?")
        a["asset_cost"] = ui.number("Q07", "Стоимость помещения, ₽", minimum=1)
    if a["goal"] == "vehicle" or a.get("asset") == "vehicle":
        a["vehicle"] = ui.text("Q08", "Какой транспорт / спецтехника? Укажите тип и состояние.")
        a["asset_cost"] = ui.number("Q08", "Стоимость объекта, ₽", minimum=1)
    if a["goal"] == "refinance":
        a["existing_loan"] = ui.yn("Q09", "Есть действующий кредит для рефинансирования?")
        if a["existing_loan"] == "yes":
            a["loan_owner"] = ui.choice("Q09", "На кого оформлен кредит?", [
                ("business", "На бизнес"), ("personal", "На физическое лицо"), ("unknown", "Не знаю")])
            a["loan_bank"] = ui.text("Q09", "В каком банке кредит и что хотите улучшить?")
            a["loan_balance"] = ui.number("Q09", "Остаток долга, ₽", minimum=1)
            a["loan_payment"] = ui.number("Q09", "Ежемесячный платёж по этому кредиту, ₽")
            a["loan_months"] = ui.number("Q09", "Оставшийся срок, месяцев", minimum=1, integer=True)
    if a["goal"] == "startup":
        a["startup_costs"] = ui.text("Q10a", "Что финансируем при запуске: товар, помещение, оборудование, транспорт?")
        a["asset_cost"] = ui.number("Q10a", "Общий бюджет запуска, ₽", minimum=1)
    a["registered"] = ui.choice("Q11", "Зарегистрированы ИП или юридическое лицо?", [
        ("yes", "Да"), ("pending", "Регистрация в процессе"), ("no", "Нет")])
    while True:
        a["form"] = ui.choice("Q12", "В какой форме работаете?", [
            ("ip", "ИП"), ("ooo", "ООО"), ("other_legal", "Другая организация"),
            ("self_employed", "Самозанятый без ИП"), ("none", "Пока без регистрации")])
        if (a["registered"] == "yes") == (a["form"] in BUSINESS_FORMS):
            break
        ui.writer("Ответы Q11 и Q12 противоречат друг другу. Укажите текущую форму, а не планируемую.")
    if a["form"] in BUSINESS_FORMS:
        while True:
            a["inn"] = ui.text("Q13", "ИНН бизнеса (реестры скрипт не проверяет)")
            needed = 12 if a["form"] == "ip" else 10
            if a["inn"] is None or (a["inn"].isascii() and a["inn"].isdigit() and len(a["inn"]) == needed):
                break
            ui.writer(f"Ожидается {needed} цифр или '?'. Проверяется только формат.")
    if a["form"] == "ip":
        a["owner_age"] = ui.number("Q35", "Сколько вам полных лет?", integer=True)
        a["citizenship"] = ui.choice("Q36", "Гражданство", [
            ("rf", "РФ"), ("other", "Другое"), ("unknown", "Не знаю / уточню")])
        a["personal_history"] = ui.choice("Q37", "Личная кредитная история", [
            ("none", "Кредитов не было"), ("clear", "Кредиты есть / были, просрочек нет"),
            ("arrears", "Есть / были просрочки"), ("unknown", "Не знаю")])
    if a["form"] in BUSINESS_FORMS:
        a["alpha_client"] = ui.choice("Q17", "Есть расчётный счёт в Альфа-Банке?", [
            ("yes", "Да"), ("no", "Нет"), ("opening", "Открываем")])
        if a["alpha_client"] == "yes":
            a["personal_offer"] = ui.yn("Q18", "Есть персональное предложение в Альфа-Бизнес?")
            if a["personal_offer"] == "yes":
                a["offer_details"] = ui.text("Q18", "Укажите продукт, сумму и срок предложения.")
            a["subscription"] = ui.yn("Q19", "Есть действующая подписка на кредит или овердрафт?")
            if a["subscription"] == "yes":
                a["subscription_target"] = ui.choice("Q20", "К какому продукту относится подписка?", [
                    ("overdraft", "Овердрафт"), ("line", "Возобновляемая линия"),
                    ("other", "Другой кредит"), ("unknown", "Не знаю")])
                a["subscription_valid"] = ui.yn("Q20", "Подтверждены срок действия и применимость к этому предложению?")
                a["subscription_details"] = ui.text("Q20", "Укажите срок действия, стоимость и условия подписки.")
    a["amount"] = ui.number("Q21", "Какая сумма нужна, ₽?", minimum=1)
    a["needed_date"] = ui.day("Q22", "Когда нужны деньги?")
    if a["goal"] == "stock":
        a["disbursement"] = ui.choice("Q23", "Деньги нужны сразу или частями?", [
            ("once", "Сразу"), ("parts", "Частями"), ("unknown", "Не знаю")])
        if a.get("regular") != "yes" and (a["disbursement"] == "parts" or a.get("regular") == "unknown"):
            a["reuse"] = ui.yn("Q24", "После возврата денег хотите снова пользоваться лимитом?")
    if a["goal"] == "investment" and a.get("disbursement") == "parts" and a.get("asset") != "vehicle":
        a["reuse"] = ui.yn("Q24", "После возврата денег хотите снова пользоваться лимитом?")
    if "gap_days" not in a:
        a["term_months"] = ui.number("Q25", "Желаемый срок договора, месяцев", minimum=1, integer=True)
        if a.get("reuse") == "yes":
            a["tranche_months"] = ui.number("Q25", "За сколько месяцев вернёте каждую выдачу?", minimum=1, integer=True)
    a["repayment_source"] = ui.choice("Q26", "Источник погашения", [
        ("revenue", "Текущая выручка"), ("contract", "Оплата по контракту"),
        ("seasonal", "Сезонные продажи"), ("other", "Другой источник"), ("unknown", "Пока не знаю")])
    if a["repayment_source"] == "other":
        a["source_details"] = ui.text("Q26", "Опишите источник и ожидаемые сроки поступления.")
    if a["goal"] in {"stock", "investment", "startup", "refinance"}:
        a["collateral"] = ui.yn("Q27", "Готовы предоставить имущество в залог?")
        if a["collateral"] == "yes":
            a["collateral_details"] = ui.text("Q27", "Вид имущества, собственник, примерная стоимость, регион, город и адрес.")
    if a["form"] in LEGAL_FORMS:
        a["guarantor"] = ui.yn("Q34", "Собственник готов выступить поручителем, если продукт этого требует?")
        if a["guarantor"] == "yes":
            a["guarantor_details"] = ui.text("Q34", "Кто готов выступить поручителем и какова доля в бизнесе, %?")
    if a["goal"] in {"investment", "property", "vehicle", "startup"}:
        a["own_funds"] = ui.yn("Q28", "Есть собственные средства для покупки / запуска?")
        if a["own_funds"] == "yes":
            a["own_amount"] = ui.number("Q28", "Сумма собственных средств, ₽")
        elif a["own_funds"] == "no":
            a["own_amount"] = 0
    a["seasonal"] = ui.yn("Q31", "Есть сезонность или значительные колебания поступлений?", unknown=False)
    if a["seasonal"] == "yes":
        a["season_details"] = ui.text("Q31", "Месяцы пиков и спадов")
        a["revenue"] = ui.number("Q29", "Средняя месячная выручка, ₽")
        a["revenue_period"] = ui.text("Q29", "За какой период рассчитана средняя выручка?")
        a["free_cash"] = ui.number("Q30", "Остаток после текущих расходов, до кредитных платежей, ₽/мес.", negative=True)
    # При «нет» Q29-Q30 полностью пропущены; ключей с нулём не создаём.
    a["debt_payments"] = ui.number("Q32", "Все ежемесячные платежи бизнеса по кредитам и лизингу, ₽ (0, если нет)")
    a["arrears"] = ui.yn("Q33", "Есть текущие просрочки бизнеса по кредитам или обязательным платежам?")
    if a["arrears"] == "yes":
        a["arrears_details"] = ui.text("Q33", "Вид, сумма и длительность просрочки")
    a["asked"] = ui.asked.copy()
    return a


@dataclass
class Candidate:
    code: str
    reason: str
    status: str = "candidate"  # candidate / clarify / excluded / separate
    checks: list[str] = field(default_factory=list)

    def clarify(self, text: str) -> None:
        if self.status == "candidate":
            self.status = "clarify"
        self.checks.append(text)

    def exclude(self, text: str) -> None:
        self.status = "excluded"
        self.checks.append(text)


def recommend(a: dict[str, Any]) -> dict[str, Any]:
    """Чистая функция: ответы -> кандидаты, объяснения, исключения, итог R01/R02/R03.

    Для отсутствующего критичного ответа возвращается уточнение. Условия,
    оставленные этапу заявки (регион, возраст бизнеса, проверка документов),
    перечисляются отдельно и не подменяются выдуманными ответами.
    """
    candidates: dict[str, Candidate] = {}
    issues: list[str] = []
    notes: list[str] = []
    def add(code: str, reason: str, *, status: str = "candidate") -> Candidate:
        if code not in candidates:
            candidates[code] = Candidate(code, reason, status)
        return candidates[code]
    def once() -> None:
        add("P01", "Нужна разовая сумма на бизнес-цель без залога.")
        add("P04", "Разовая сумма; возможна залоговая программа.")
        c = add("P11", "Дополнительный вариант разового финансирования.", status="clarify")
        c.checks.append("Лимит до 50 млн ₽ без залога из замечаний команды не подтверждён. Уточнить условия банка.")
    def revolving() -> None:
        add("P03", "Нужны повторные выдачи и восстановление лимита после возврата.")
        add("P05", "Тот же оборотный сценарий с залогом.")
    def gap() -> None:
        days = a.get("gap_days")
        start = a.get("needed_date") or a.get("today") or date.today()
        six_month_days = (add_months(start, 6) - start).days
        od_sub = (a.get("alpha_client") == "yes" and a.get("subscription") == "yes"
                  and a.get("subscription_target") == "overdraft" and a.get("subscription_valid") == "yes")
        if days is None:
            issues.append("Q05: неизвестна дата поступления денег для возврата.")
            add("P02", "Возможен при коротком кассовом разрыве.", status="clarify")
            add("P03", "Возможна линия при более длинном разрыве.", status="clarify")
        elif days <= (90 if od_sub else 60):
            add("P02", f"Кассовый разрыв {days} дней укладывается в транш {'до 90' if od_sub else 'до 60'} дней.")
        elif days <= six_month_days:
            add("P03", f"Разрыв {days} дней длиннее доступного транша овердрафта, но не более 6 календарных месяцев.")
        else:
            c = add("P03", "Разрыв дольше базового транша до 6 месяцев.", status="clarify")
            c.checks.append("Проверить расширенные условия линии или другой продукт. Автоматически не одобрять.")
        if a.get("receivable") == "yes":
            add("A03", "Оплата ожидается по уже поставленному товару / оказанной услуге.")
        elif a.get("receivable") in {None, "unknown"}:
            notes.append("Применимость факторинга неизвестна: нужна подтверждённая дебиторская задолженность.")

    goal = a.get("goal")
    if goal == "stock":
        if a.get("regular") == "yes" or a.get("reuse") == "yes":
            revolving()
        elif a.get("disbursement") == "parts" and a.get("reuse") == "no":
            add("P06", "Закупки по этапам; повторное использование лимита не требуется.")
        elif a.get("regular") == "no" and a.get("disbursement") == "once":
            once()
        else:
            issues.append("Q04 / Q23 / Q24: уточнить регулярность закупки и способ выдачи.")
    elif goal == "gap":
        gap()
    elif goal == "investment":
        if a.get("asset") == "vehicle":
            add("P09", "Покупается транспорт или спецтехника, даже если исходная цель — оборудование.")
            add("A01", "Лизинг транспорта / спецтехники как альтернатива покупке в кредит.")
        else:
            if a.get("disbursement") == "once":
                once()
            elif a.get("disbursement") == "parts" and a.get("reuse") == "no":
                add("P06", "Расходы на развитие по этапам, без восстановления лимита.")
            elif a.get("disbursement") == "parts" and a.get("reuse") == "yes":
                issues.append("Возобновляемые P03 / P05 имеют оборотную цель: инвестиционную цель нужно уточнить.")
                add("P06", "Возможное поэтапное финансирование развития; лимит не возобновляется.", status="clarify")
            else:
                issues.append("Q06: уточнить, нужна сумма сразу или по этапам.")
            if a.get("asset") == "equipment":
                add("A01", "Лизинг производственного оборудования как альтернатива.")
            elif a.get("asset") == "unknown":
                notes.append("Q06a: уточнить объект; для транспорта возможен P09.")
    elif goal == "property":
        c = add("P08", "Покупка помещения для бизнеса.")
        if a.get("property_chosen") != "yes" or a.get("asset_cost") is None:
            c.clarify("Q07: нужны объект и стоимость помещения.")
    elif goal == "vehicle":
        add("P09", "Покупка транспорта или спецтехники.")
        add("A01", "Лизинг как альтернатива кредиту на покупку.")
    elif goal == "startup":
        add("P10", "Запуск нового зарегистрированного бизнеса с подходящим залогом.")
        if not a.get("startup_costs") or a.get("asset_cost") is None:
            issues.append("Q10a: уточнить назначение стартовых расходов и бюджет.")
    elif goal == "refinance":
        if a.get("existing_loan") != "yes":
            issues.append("Q09: рефинансирование требует действующего кредита; уточните цель.")
        elif a.get("loan_owner") == "business":
            c = add("P07", "Нужно заменить существующий кредит бизнеса.")
            if any(a.get(k) is None for k in ("loan_balance", "loan_payment", "loan_months")):
                c.clarify("Q09: нужны остаток, платёж и оставшийся срок.")
        elif a.get("loan_owner") == "personal" and a.get("form") == "self_employed":
            add("P13", "Действующий личный кредит самозанятого; самостоятельная программа.", status="separate")
        else:
            issues.append("Q09: уточнить заёмщика и применимость программы личного рефинансирования.")
    elif goal == "contract":
        if a.get("contract_need") in {"guarantee", "both"}:
            add("A02", "Требуется обеспечить обязательства по контракту, а не получить деньги по гарантии.")
        if a.get("contract_need") in {"money", "both"}:
            gap()
        if a.get("contract_need") not in {"guarantee", "money", "both"}:
            issues.append("Q10b: уточнить, нужны деньги, гарантия или оба решения.")
    else:
        issues.append("Q10: цель нужно уточнить. Свободный текст не классифицируется по случайным ключевым словам.")

    for c in candidates.values():
        code = c.code
        if code in PERSONAL:
            continue
        if a.get("form") not in BUSINESS_FORMS or a.get("registered") != "yes":
            if code == "P10" and a.get("registered") == "pending":
                c.clarify("Бизнес ещё не зарегистрирован; сначала завершить регистрацию.")
            else:
                c.exclude("Для этого бизнес-маршрута не подтверждён статус зарегистрированного ИП / юрлица.")
        if code in SECURED:
            if a.get("collateral") == "no":
                c.exclude("Нет залога, обязательного для этого варианта.")
            elif a.get("collateral") != "yes":
                c.clarify("Q27: наличие залога неизвестно.")
            else:
                c.checks.append("Проверить приемлемость, оценку, права и местонахождение залога.")
                if not a.get("collateral_details"):
                    c.clarify("Q27: не заполнены сведения об имуществе и его адрес.")
        if a.get("form") in LEGAL_FORMS and code in {"P01", "P02", "P03", "P04", "P05", "P06"}:
            if a.get("guarantor") == "no":
                c.exclude("Собственник не готов к обязательному поручительству по рассматриваемой программе.")
            elif a.get("guarantor") != "yes":
                c.clarify("Q34: готовность собственника к поручительству не подтверждена.")
            else:
                c.checks.append("Проверить состав поручителей и доли собственников.")
        if a.get("form") == "ip" and code in {"P01", "P02", "P03", "P04", "P05", "P06"}:
            if a.get("owner_age") is not None and a["owner_age"] < 18:
                c.exclude("Возраст ИП меньше 18 лет.")
            elif a.get("owner_age") is None:
                c.clarify("Q35: возраст ИП неизвестен.")
            if a.get("citizenship") == "other":
                c.exclude("Требование гражданства РФ не выполнено.")
            elif a.get("citizenship") != "rf":
                c.clarify("Q36: гражданство не подтверждено.")
        amount = a.get("amount")
        rule = RULES.get(code, {})
        if amount is None:
            c.clarify("Q21: сумма запроса неизвестна.")
        else:
            if "min" in rule and amount < rule["min"]:
                c.exclude(f"Сумма ниже минимальной по карте: {rule['min']:,.0f} ₽.")
            if "max" in rule and amount > rule["max"]:
                c.exclude(f"Сумма выше максимальной по карте: {rule['max']:,.0f} ₽.")
        if "months" in rule and a.get("term_months") is not None:
            low, high = rule["months"]
            if not low <= a["term_months"] <= high:
                c.exclude(f"Срок вне диапазона {low}–{high} месяцев для этого продукта.")
        if code in {"P09", "A01"} and a.get("asset_cost") is None:
            c.clarify("Неизвестна стоимость приобретаемого объекта.")
        if code in {"P03", "P05"} and a.get("tranche_months") is not None and a["tranche_months"] > 6:
            c.clarify("Срок выдачи дольше базовых 6 месяцев: проверить расширенные условия конкретной линии.")
        if code not in RULES:
            c.checks.append("Точные рамки суммы, срока и условий сверить с конкретным предложением.")

    # Общая резервная ветка Q21: не объявлять отсутствие вариантов только
    # из-за ограничения 30 млн у P01/P03. P11 всё ещё требует подтверждения.
    amount = a.get("amount")
    monetary_goal = goal in {"stock", "gap", "investment"} or (goal == "contract" and a.get("contract_need") in {"money", "both"})
    if (amount is not None and 30_000_000 < amount <= 50_000_000
            and a.get("collateral") != "yes" and monetary_goal
            and a.get("form") in BUSINESS_FORMS and a.get("registered") == "yes"):
        c = add("P11", "Q21: запрос 30–50 млн ₽ без залога требует проверки дополнительного предложения.", status="clarify")
        c.clarify("Подтвердить лимит и допустимую цель P11; не подменять уточнение автоматическим отказом.")
    # Выбор физлица не подменяет бизнес-заёмщика: только направление в отдельную программу.
    business_left = any(c.status in {"candidate", "clarify"} and c.code.startswith("P") for c in candidates.values())
    if not business_left and goal != "refinance":
        if a.get("form") == "self_employed":
            add("P14", "Самозанятый без ИП: отдельное оформление личного кредита.", status="separate")
        elif a.get("form") == "none":
            add("P12", "Нет зарегистрированного бизнеса: можно отдельно рассмотреть личную программу.", status="separate")
        elif goal == "startup" and a.get("collateral") == "no":
            add("P12", "Для нового бизнеса без залога личная программа может быть отдельной альтернативой.", status="separate")
    if a.get("preferred") in PERSONAL:
        add(a["preferred"], "Выбрана самостоятельная личная программа; вопросы этой ветки удалены из алгоритма.", status="separate")
    if a.get("amount") is None:
        issues.append("Неизвестна требуемая сумма.")
    if a.get("repayment_source") in {None, "unknown"}:
        issues.append("Q26: источник погашения не определён.")
    if a.get("repayment_source") == "other" and not a.get("source_details"):
        issues.append("Q26: другой источник погашения не описан.")
    if a.get("needed_date") is None:
        issues.append("Q22: дата получения денег неизвестна.")
    if "gap_days" not in a and a.get("term_months") is None:
        issues.append("Q25: желаемый срок договора неизвестен.")
    if a.get("reuse") == "yes" and "gap_days" not in a and a.get("tranche_months") is None:
        issues.append("Q25: неизвестен срок возврата отдельной выдачи по линии.")
    if a.get("needed_date") and a["needed_date"] < (a.get("today") or date.today()):
        issues.append("Q22: желаемая дата выдачи уже прошла; уточнить срок.")
    if a.get("seasonal") == "no":
        notes.append("Q29–Q30 пропущены по Q31. Выручка и остаток неизвестны, не равны нулю; потребуются в заявке.")
    if a.get("arrears") in {"yes", "unknown"} or a.get("personal_history") in {"arrears", "unknown"}:
        notes.append("Просрочки / кредитная история требуют проверки. В карте нет правила автоматического отказа по ним.")
    if a.get("personal_offer") == "yes":
        notes.append("Сравнить результат с персональным предложением: " + (a.get("offer_details") or "параметры пока неизвестны"))
    if goal in {"property", "vehicle", "investment", "startup"}:
        notes.append("Собственные средства учитываются в бюджете; обязательный взнос не задан универсально для всех продуктов.")
    notes.extend([
        "Возраст и регион бизнеса, фактическую деятельность, документы и финансовые показатели проверяют при заявке.",
        "Скрипт не проверяет реестры, залог, БКИ или персональные предложения банка.",
    ])
    all_candidates = list(candidates.values())
    preferred = a.get("preferred")
    all_candidates.sort(key=lambda c: (c.status != "candidate", c.code != preferred, c.code.startswith("A")))
    ready = [c for c in all_candidates if c.status == "candidate"]
    pending = [c for c in all_candidates if c.status == "clarify"]
    separate = [c for c in all_candidates if c.status == "separate"]
    # Для «деньги + гарантия» одной гарантии недостаточно для завершения подбора.
    if goal == "contract" and a.get("contract_need") == "both" and not any(c.code.startswith("P") for c in ready):
        issues.append("Для контракта требуется ещё денежное финансирование; одной гарантии недостаточно.")
    if issues or (not ready and (pending or separate)):
        status = "R02"
    elif ready:
        status = "R01"
    else:
        status = "R03"
    return {"status": status, "candidates": all_candidates,
            "issues": list(dict.fromkeys(issues)), "notes": list(dict.fromkeys(notes)),
            "preferred": preferred, "rules_as_of": RULES_AS_OF}


def show_result(result: dict[str, Any], writer: Callable[[str], None] = print) -> None:
    labels = {"R01": "Предварительно подходящие типы продуктов",
              "R02": "Нужно уточнение условий / данных или отдельное оформление",
              "R03": "В проверенных ветках карты вариантов не найдено"}
    writer("\n" + "=" * 68)
    writer(f"{result['status']}: {labels[result['status']]}")
    groups = [("candidate", "Предварительные кандидаты"), ("clarify", "Условия требуют уточнения"),
              ("separate", "Отдельные программы; не замена заёмщика"), ("excluded", "Почему другие варианты исключены")]
    for status, title in groups:
        rows = [c for c in result["candidates"] if c.status == status]
        if not rows:
            continue
        writer("\n" + title + ":")
        for c in rows:
            writer(f"  {c.code}. {PRODUCTS[c.code]}")
            writer("    Причина: " + c.reason)
            for check in c.checks:
                writer("    • " + check)
    if result["preferred"] and result["preferred"] not in {c.code for c in result["candidates"]}:
        writer("\nВыбранный в Q02 продукт не получился из указанной цели. Выбор сохранён, но не навязывается.")
    for title, key in [("Что уточнить", "issues"), ("Что остаётся проверить", "notes")]:
        if result[key]:
            writer("\n" + title + ":")
            for item in result[key]:
                writer("  • " + item)
    writer("\nR04 — следующий шаг после выбора продукта: заявка, согласия, документы, проверки и скоринг.")
    writer("Это определение типа финансирования, а не одобрение кредита. Условия карты: " + RULES_AS_OF + ".")


def demo_answers(name: str) -> dict[str, Any]:
    a: dict[str, Any] = {
        "today": date.today(), "knowledge": "no", "goal": "gap", "registered": "yes", "form": "ip",
        "inn": "123456789012", "owner_age": 30, "citizenship": "rf", "personal_history": "clear",
        "alpha_client": "no", "amount": 2_000_000, "needed_date": date.today(),
        "gap_days": 45, "receivable": "no", "repayment_source": "revenue",
        "collateral": "no", "seasonal": "no", "debt_payments": 0, "arrears": "no",
    }
    if name == "line":
        a["gap_days"] = 120
    elif name == "startup":
        a.update(goal="startup", collateral="yes", collateral_details="Коммерческое помещение, собственник ИП, адрес указан",
                 startup_costs="Оборудование и первая закупка", asset_cost=3_000_000, own_funds="yes", own_amount=1_000_000, term_months=36)
        a.pop("gap_days")
    elif name == "equipment":
        a.update(goal="investment", asset="vehicle", vehicle="Подержанный погрузчик", asset_cost=2_500_000,
                 disbursement="once", own_funds="yes", own_amount=500_000, term_months=36)
        a.pop("gap_days")
    return a


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Диалог подбора типа финансирования по карте Альфахака (Python 3.10+).")
    parser.add_argument("--demo", choices=["overdraft", "line", "startup", "equipment"], help="Показать пример без ввода")
    parser.add_argument("--products", action="store_true", help="Показать каталог продуктов")
    args = parser.parse_args(argv)
    if args.products:
        for code, title in PRODUCTS.items():
            print(f"{code}: {title}")
        return 0
    try:
        if args.demo:
            answers = demo_answers(args.demo)
            print("Демонстрационный сценарий: " + args.demo)
        else:
            print("Подбор типа финансирования. Ответы не сохраняются и никуда не отправляются.")
            print("Выбирайте номер; в числовых / текстовых полях '?' означает «не знаю». Выход: 'выход' или Ctrl+C.")
            answers = collect_answers(Terminal())
        show_result(recommend(answers))
        return 0
    except Cancelled:
        print("\nДиалог завершён. Ответы не сохранены.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
