"""One recommendation result; never relabel financial stability as bank approval."""
import re


def customer_text(text):
    return re.sub(r'^Q\d+[a-z]?(?:[–-]Q\d+)?\s*:\s*', '', text)


def combined(recommendation, scoring, inn):
    if recommendation is None:
        return None
    candidates = [dict(c) for c in recommendation['candidates'] if c['status'] != 'excluded']
    prediction = (scoring or {}).get('prediction')
    financial_status = (scoring or {}).get('status', 'not_requested')
    if inn and len(inn) == 12:
        financial_status = 'not_applicable'
    state, title = 'needs_information', 'Уточните данные для подбора'
    if not candidates:
        state, title = 'no_match', 'Подходящих вариантов пока нет'
    elif recommendation['status'] == 'R01':
        state, title = 'products_selected', 'Подобрали варианты финансирования'
    if candidates and prediction and prediction['predicted_class'] == 0:
        state, title = 'risk_review', 'Есть варианты, но финансовый профиль требует проверки'
    elif candidates and recommendation['status'] == 'R01' and prediction:
        state, title = 'favorable_profile', 'Варианты подходят по анкете и финансовому профилю'
    for product in candidates:
        product['readiness'] = ('risk_review' if state == 'risk_review' else
                                'clarify' if product['status'] in ('clarify', 'separate') or recommendation['status'] != 'R01'
                                else 'candidate')
    return {
        'status': state, 'title': title, 'is_bank_decision': False,
        'products': candidates,
        'financial_assessment': {
            'status': financial_status,
            'probability_financial_stability': prediction['probability_financial_stability'] if prediction else None,
            'report_year': (scoring or {}).get('report_year'),
            'time_period_validation': prediction.get('time_period_validation') if prediction else None,
        },
        'approval_probability': {
            'status': 'unavailable', 'value': None, 'reason': 'approval_model_not_available',
            'message': 'Нет модели, обученной на фактических одобрениях и отказах банка.',
        },
        'clarifications': [customer_text(x) for x in recommendation['issues']],
    }
