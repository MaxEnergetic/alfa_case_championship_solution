"""Adaptive question order from the supplied credit_dialog.py, without terminal I/O."""
from datetime import date
from typing import Any, Generator

from .questions import Question, choice, yn, number, text, day
from .rules import PRODUCTS, BUSINESS_FORMS, LEGAL_FORMS


def purchase_amount_question():
    return number('amount', 'Q21', 'Из этой стоимости хотите взять в кредит, ₽', minimum=1)


def questions(today: date, version: int = 1) -> Generator[Question, Any, dict[str, Any]]:
    a: dict[str, Any] = {'today': today}
    a['knowledge'] = (yield choice('knowledge', 'Q01', 'Вы уже знаете, какой кредит хотите?', [('yes', 'Да, знаю'), ('no', 'Нет, помогите выбрать'), ('compare', 'Есть вариант, хочу сравнить')]))
    if a['knowledge'] != 'no':
        a['preferred'] = (yield choice('preferred', 'Q02', 'Какой продукт рассматриваете?', list(PRODUCTS.items()) + [('unknown', 'Другое / не уверен')]))
    goals = [('stock', 'Купить товар или сырьё'), ('gap', 'Оплатить расходы до выручки'), ('investment', 'Оборудование, ремонт или расширение'), ('property', 'Купить помещение'), ('vehicle', 'Транспорт или спецтехника'), ('refinance', 'Заменить действующий кредит'), ('startup', 'Запустить новый бизнес'), ('contract', 'Обеспечить контракт / тендер'), ('other', 'Другая цель / не определился')]
    a['goal'] = (yield choice('goal', 'Q03', 'На что нужны деньги? Выберите основную цель.', goals))
    if a['goal'] == 'other':
        a['description'] = (yield text('description', 'Q10', 'Опишите потребность. Свободный текст не классифицируется автоматически.'))
    if a['goal'] == 'stock':
        a['regular'] = (yield choice('regular', 'Q04', 'Закупка разовая или регулярная?', [('no', 'Разовая'), ('yes', 'Регулярная'), ('unknown', 'Не знаю')]))
        if a['regular'] == 'yes':
            a['reuse'] = 'yes'
    if a['goal'] == 'contract':
        a['contract_need'] = (yield choice('contract_need', 'Q10b', 'Нужны деньги или обеспечение обязательства?', [('money', 'Деньги на исполнение'), ('guarantee', 'Гарантия'), ('both', 'И деньги, и гарантия')]))
        a['contract_details'] = (yield text('contract_details', 'Q10b', 'Укажите обязательство, сумму и срок гарантии / контракта.'))
    if a['goal'] == 'gap' or (a['goal'] == 'contract' and a['contract_need'] != 'guarantee'):
        a['gap_days'] = (yield number('gap_days', 'Q05', 'Через сколько дней после выдачи поступят деньги для возврата?', minimum=1, integer=True))
        a['receivable'] = (yield yn('receivable', 'Q05a', 'Оплата ожидается за уже поставленный товар или оказанную услугу?'))
    if a['goal'] == 'investment':
        a['disbursement'] = (yield choice('disbursement', 'Q06', 'Вся сумма нужна сразу или по этапам?', [('once', 'Сразу'), ('parts', 'По этапам'), ('unknown', 'Не знаю')]))
        a['asset'] = (yield choice('asset', 'Q06a', 'Что именно покупаете?', [('equipment', 'Производственное оборудование'), ('vehicle', 'Транспорт / спецтехника'), ('other', 'Другой объект / ремонт без покупки объекта'), ('unknown', 'Не знаю')]))
    if a['goal'] == 'property':
        a['property_chosen'] = (yield yn('property_chosen', 'Q07', 'Помещение уже выбрано?'))
        a['asset_cost'] = (yield number('asset_cost', 'Q07', 'Стоимость помещения, ₽', minimum=1))
    if a['goal'] == 'vehicle' or a.get('asset') == 'vehicle':
        a['vehicle'] = (yield text('vehicle', 'Q08', 'Какой транспорт / спецтехника? Укажите тип и состояние.'))
        a['asset_cost'] = (yield number('asset_cost', 'Q08', 'Стоимость объекта, ₽', minimum=1))
    if a['goal'] == 'refinance':
        a['existing_loan'] = (yield yn('existing_loan', 'Q09', 'Есть действующий кредит для рефинансирования?'))
        if a['existing_loan'] == 'yes':
            a['loan_owner'] = (yield choice('loan_owner', 'Q09', 'На кого оформлен кредит?', [('business', 'На бизнес'), ('personal', 'На физическое лицо'), ('unknown', 'Не знаю')]))
            a['loan_bank'] = (yield text('loan_bank', 'Q09', 'В каком банке кредит и что хотите улучшить?'))
            a['loan_balance'] = (yield number('loan_balance', 'Q09', 'Остаток долга, ₽', minimum=1))
            a['loan_payment'] = (yield number('loan_payment', 'Q09', 'Ежемесячный платёж по этому кредиту, ₽'))
            a['loan_months'] = (yield number('loan_months', 'Q09', 'Оставшийся срок, месяцев', minimum=1, integer=True))
    if a['goal'] == 'startup':
        a['startup_costs'] = (yield text('startup_costs', 'Q10a', 'Что финансируем при запуске: товар, помещение, оборудование, транспорт?'))
        a['asset_cost'] = (yield number('asset_cost', 'Q10a', 'Общий бюджет запуска, ₽', minimum=1))
    if version >= 2 and 'asset_cost' in a:
        a['amount'] = (yield purchase_amount_question())
    a['registered'] = (yield choice('registered', 'Q11', 'Зарегистрированы ИП или юридическое лицо?', [('yes', 'Да'), ('pending', 'Регистрация в процессе'), ('no', 'Нет')]))
    a['form'] = (yield choice('form', 'Q12', 'В какой форме работаете?', [('ip', 'ИП'), ('ooo', 'ООО'), ('other_legal', 'Другая организация'), ('self_employed', 'Самозанятый без ИП'), ('none', 'Пока без регистрации')]))
    if a['form'] in BUSINESS_FORMS:
        a['inn'] = (yield text('inn', 'Q13', 'Укажите ИНН бизнеса для получения сведений из реестров.'))
    if a['form'] == 'ip':
        a['owner_age'] = (yield number('owner_age', 'Q35', 'Сколько вам полных лет?', integer=True))
        a['citizenship'] = (yield choice('citizenship', 'Q36', 'Гражданство', [('rf', 'РФ'), ('other', 'Другое'), ('unknown', 'Не знаю / уточню')]))
        a['personal_history'] = (yield choice('personal_history', 'Q37', 'Личная кредитная история', [('none', 'Кредитов не было'), ('clear', 'Кредиты есть / были, просрочек нет'), ('arrears', 'Есть / были просрочки'), ('unknown', 'Не знаю')]))
        # PDF page 5 requires these details; they were missing in the terminal script.
        if a['personal_history'] == 'arrears':
            a['personal_history_details'] = (yield text('personal_history_details', 'Q37', 'Укажите вид и длительность личных просрочек.'))
    if a['form'] in BUSINESS_FORMS:
        a['alpha_client'] = (yield choice('alpha_client', 'Q17', 'Есть расчётный счёт в Альфа-Банке?', [('yes', 'Да'), ('no', 'Нет'), ('opening', 'Открываем')]))
        if a['alpha_client'] == 'yes':
            a['personal_offer'] = (yield yn('personal_offer', 'Q18', 'Есть персональное предложение в Альфа-Бизнес?'))
            if a['personal_offer'] == 'yes':
                a['offer_details'] = (yield text('offer_details', 'Q18', 'Укажите продукт, сумму и срок предложения.'))
            a['subscription'] = (yield yn('subscription', 'Q19', 'Есть действующая подписка на кредит или овердрафт?'))
            if a['subscription'] == 'yes':
                a['subscription_target'] = (yield choice('subscription_target', 'Q20', 'К какому продукту относится подписка?', [('overdraft', 'Овердрафт'), ('line', 'Возобновляемая линия'), ('other', 'Другой кредит'), ('unknown', 'Не знаю')]))
                a['subscription_valid'] = (yield yn('subscription_valid', 'Q20', 'Подтверждены срок действия и применимость к этому предложению?'))
                a['subscription_details'] = (yield text('subscription_details', 'Q20', 'Укажите срок действия, стоимость и условия подписки.'))
    if 'amount' not in a:
        a['amount'] = (yield number('amount', 'Q21', 'Какая сумма нужна, ₽?', minimum=1))
    a['needed_date'] = (yield day('needed_date', 'Q22', 'Когда нужны деньги?'))
    if a['goal'] == 'stock':
        a['disbursement'] = (yield choice('disbursement', 'Q23', 'Деньги нужны сразу или частями?', [('once', 'Сразу'), ('parts', 'Частями'), ('unknown', 'Не знаю')]))
        if a.get('regular') != 'yes' and (a['disbursement'] == 'parts' or a.get('regular') == 'unknown'):
            a['reuse'] = (yield yn('reuse', 'Q24', 'После возврата денег хотите снова пользоваться лимитом?'))
    if a['goal'] == 'investment' and a.get('disbursement') == 'parts' and (a.get('asset') != 'vehicle'):
        a['reuse'] = (yield yn('reuse', 'Q24', 'После возврата денег хотите снова пользоваться лимитом?'))
    if 'gap_days' not in a:
        a['term_months'] = (yield number('term_months', 'Q25', 'Желаемый срок договора, месяцев', minimum=1, integer=True))
        if a.get('reuse') == 'yes':
            a['tranche_months'] = (yield number('tranche_months', 'Q25', 'За сколько месяцев вернёте каждую выдачу?', minimum=1, integer=True))
    a['repayment_source'] = (yield choice('repayment_source', 'Q26', 'Источник погашения', [('revenue', 'Текущая выручка'), ('contract', 'Оплата по контракту'), ('seasonal', 'Сезонные продажи'), ('other', 'Другой источник'), ('unknown', 'Пока не знаю')]))
    if a['repayment_source'] == 'other':
        a['source_details'] = (yield text('source_details', 'Q26', 'Опишите источник и ожидаемые сроки поступления.'))
    if a['goal'] in {'stock', 'investment', 'startup', 'refinance'}:
        a['collateral'] = (yield yn('collateral', 'Q27', 'Готовы предоставить имущество в залог?'))
        if a['collateral'] == 'yes':
            a['collateral_details'] = (yield text('collateral_details', 'Q27', 'Вид имущества, собственник, примерная стоимость, регион, город и адрес.'))
    if a['form'] in LEGAL_FORMS:
        a['guarantor'] = (yield yn('guarantor', 'Q34', 'Собственник готов выступить поручителем, если продукт этого требует?'))
        if a['guarantor'] == 'yes':
            a['guarantor_details'] = (yield text('guarantor_details', 'Q34', 'Кто готов выступить поручителем и какова доля в бизнесе, %?'))
    if a['goal'] in {'investment', 'property', 'vehicle', 'startup'}:
        a['own_funds'] = (yield yn('own_funds', 'Q28', 'Есть собственные средства для покупки / запуска?'))
        if a['own_funds'] == 'yes':
            a['own_amount'] = (yield number('own_amount', 'Q28', 'Сумма собственных средств, ₽'))
        elif a['own_funds'] == 'no':
            a['own_amount'] = 0
    a['seasonal'] = (yield yn('seasonal', 'Q31', 'Есть сезонность или значительные колебания поступлений?', unknown=False))
    if a['seasonal'] == 'yes':
        a['season_details'] = (yield text('season_details', 'Q31', 'Месяцы пиков и спадов'))
        a['revenue'] = (yield number('revenue', 'Q29', 'Средняя месячная выручка, ₽'))
        a['revenue_period'] = (yield text('revenue_period', 'Q29', 'За какой период рассчитана средняя выручка?'))
        a['free_cash'] = (yield number('free_cash', 'Q30', 'Остаток после текущих расходов, до кредитных платежей, ₽/мес.', negative=True))
    a['debt_payments'] = (yield number('debt_payments', 'Q32', 'Все ежемесячные платежи бизнеса по кредитам и лизингу, ₽ (0, если нет)'))
    a['arrears'] = (yield yn('arrears', 'Q33', 'Есть текущие просрочки бизнеса по кредитам или обязательным платежам?'))
    if a['arrears'] == 'yes':
        a['arrears_details'] = (yield text('arrears_details', 'Q33', 'Вид, сумма и длительность просрочки'))
    return a
