"""Product selection adapted from user-supplied credit_dialog.py (23.10.2026).

Rules are a dated decision-map snapshot, not bank approval or live tariffs.
"""


import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any


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


def add_months(start: date, months: int) -> date:
    """Календарные месяцы, а не приближение «6 месяцев = 180 дней»."""
    year, month0 = divmod(start.year * 12 + start.month - 1 + months, 12)
    month = month0 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


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
        "Подбор не подтверждает залог, БКИ или персональные предложения банка; данные реестров сохраняются отдельно.",
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
