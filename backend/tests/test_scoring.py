from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fns_parser.api import ApiSettings, create_app
from fns_parser.scoring.service import ScoringService, infer
from fns_parser.scoring.upstream.core import build_features, LINES
from fns_parser.scoring.upstream.rfsd import SourceError
from tests.test_credit import BASE

REFERENCE = json.loads((Path(__file__).parent / 'fixtures/scoring_5505223431.json').read_text(encoding='utf-8'))


def snapshot():
    ref = deepcopy(REFERENCE)
    row = {'inn': ref['inn'], 'ogrn': ref['registry']['ogrn'],
           'creation_date': ref['registry']['registered_on'], 'okved': ref['registry']['main_okved'],
           'okved_section': ref['registry']['okved_section'], 'dissolution_date': None,
           **ref['quality_flags']}
    row.update({'line_' + code: ref['financials_rub'][name] / 1000 for code, name in LINES.items()})
    return {'inn': ref['inn'], 'year': 2025, 'source': ref['source'], 'row': row}


class StubClient:
    def __init__(self, value=None):
        self.value = snapshot() if value is None else value

    def fetch(self, *args, **kwargs):
        return deepcopy(self.value)


class ScoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ScoringService(ApiSettings())

    def test_feature_parity_with_upstream_real_company(self):
        result = build_features(snapshot(), '2026-10-25')
        self.assertEqual(result['features'], REFERENCE['features'])
        self.assertEqual(result['feature_coverage']['known'], 14)

    @unittest.skipUnless(importlib.util.find_spec('sklearn'), 'Install requirements-scoring.txt')
    def test_actual_model_matches_published_prediction(self):
        result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25), client=StubClient())
        self.assertEqual(result['status'], 'ready')
        self.assertAlmostEqual(result['prediction']['probability_financial_stability'],
                               REFERENCE['prediction']['probability_financial_stability'], places=12)
        self.assertEqual(result['prediction']['predicted_class'], 1)
        self.assertEqual(result['prediction']['time_period_validation'], 'not_validated')
        self.assertIsNone(result['prediction']['target_financial_year'])
        self.assertFalse(result['is_bank_decision'])

    def test_bad_financials_and_mismatched_registration_never_reach_model(self):
        for key, value in [('line_1300', None), ('line_1700', 1), ('filed', 0), ('imputed', 1),
                           ('articulated', 0), ('outlier', 1), ('financial', 1), ('ogrn', '1124028002273')]:
            with self.subTest(key=key), patch('fns_parser.scoring.service.infer') as predict:
                data = snapshot()
                data['row'][key] = value
                result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25),
                    expected_ogrn=REFERENCE['registry']['ogrn'], client=StubClient(data))
                self.assertEqual(result['status'], 'insufficient_data')
                self.assertFalse(result['can_predict'])
                self.assertIsNone(result['prediction'])
                predict.assert_not_called()

    def test_future_source_stale_data_and_closed_registry_block_prediction(self):
        data = snapshot()
        data['source']['available_by'] = '2027-01-01'
        result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25), client=StubClient(data))
        self.assertIn('source_version_published_after_as_of', result['quality_issues'])
        data = snapshot()
        data['year'] = 2022
        result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25), client=StubClient(data))
        self.assertIn('financial_report_too_old', result['quality_issues'])
        compact = {'checked_at': '2026-10-25', 'entities': [{
            'inn': REFERENCE['inn'], 'terminated_on': '2026-10-01'}]}
        result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25), client=StubClient(), compact=compact)
        self.assertIn('registry_terminated_on', result['quality_issues'])
        self.assertIsNone(result['prediction'])

    def test_optional_zero_and_missing_remain_distinct(self):
        data = snapshot()
        data['row']['line_1250'] = 0
        data['row']['line_2330'] = 0
        result = build_features(data, '2026-10-25')
        self.assertEqual(result['features']['pit_cash_to_assets_missing'], 0)
        self.assertEqual(result['features']['stability_operating_profit_to_interest_missing'], 1)
        data['row']['line_1250'] = None
        self.assertEqual(build_features(data, '2026-10-25')['features']['pit_cash_to_assets_missing'], 1)

    def test_source_failure_and_unknown_company_are_not_negative_predictions(self):
        with patch.object(StubClient, 'fetch', side_effect=SourceError('RFSD HTTP 503')):
            result = self.service.score(REFERENCE['inn'], client=StubClient())
        self.assertEqual(result['status'], 'source_unavailable')
        self.assertIsNone(result['prediction'])
        data = snapshot()
        data['row'] = None
        result = self.service.score(REFERENCE['inn'], as_of=date(2026, 10, 25), client=StubClient(data))
        self.assertFalse(result['can_predict'])


class ScoringApiTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        self.app = create_app(ApiSettings(credit_db=folder / 'sessions.db', captcha_db=folder / 'captcha.db',
                                         scoring_cache_dir=folder / 'scoring', download_dir=folder / 'pdf'))
        self.client = self.enterContext(TestClient(self.app))
        self.result = {**deepcopy(REFERENCE), 'is_bank_decision': False}

    def wait(self, job):
        for _ in range(400):
            response = self.client.get(job['status_url'])
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result['state'] not in ('running', 'waiting_for_captcha'):
                self.assertEqual(result['state'], 'completed', result)
                return result['result']
            time.sleep(.005)
        self.fail('Scoring job did not finish')

    def session(self):
        result = self.client.post('/api/v1/credit/sessions').json()
        values = {**BASE, 'inn': REFERENCE['inn']}
        while result['next_question']:
            question = result['next_question']['id']
            response = self.client.post('/api/v1/credit/sessions/' + result['session_id'] + '/answers',
                json={'revision': result['revision'], 'question_id': question, 'value': values[question]})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
        return result

    def test_inn_job_uses_model_service_without_fns(self):
        with patch.object(self.app.state.scoring, 'score', return_value=self.result) as score, \
             patch('fns_parser.api.parse_company') as parser:
            response = self.client.post('/api/v1/scoring/jobs', json={'identifier': REFERENCE['inn']})
            self.assertEqual(response.status_code, 202, response.text)
            result = self.wait(response.json())
            self.assertEqual(result['prediction'], REFERENCE['prediction'])
            parser.assert_not_called()
            self.assertEqual(score.call_args.args[0], REFERENCE['inn'])

    def test_ogrn_is_resolved_using_existing_parser(self):
        registry = {'schema_version': 'compact-1.0', 'checked_at': '2026-10-25', 'sources': {},
                    'entities': [{'inn': REFERENCE['inn'], 'ogrn': REFERENCE['registry']['ogrn'], 'entity_type': 'legal'}]}
        with patch.object(self.app.state.scoring, 'score', return_value=self.result) as score, \
             patch('fns_parser.api.parse_company', return_value=registry) as parser:
            response = self.client.post('/api/v1/scoring/jobs', json={'identifier': REFERENCE['registry']['ogrn']})
            self.assertEqual(response.status_code, 202)
            self.wait(response.json())
            self.assertEqual(score.call_args.kwargs['expected_ogrn'], REFERENCE['registry']['ogrn'])
            self.assertIsNotNone(parser.call_args.kwargs['captcha_handler'])
            self.assertFalse(parser.call_args.kwargs['enrich'])

    def test_wrong_inn_ip_period_and_extra_model_features_rejected(self):
        for body in [{'identifier': '5505223432'}, {'identifier': '784806113663'},
                     {'identifier': REFERENCE['inn'], 'as_of': '2099-01-01'},
                     {'identifier': REFERENCE['inn'], 'as_of': '2025-01-01', 'year': 2025},
                     {'identifier': REFERENCE['inn'], 'target_financial_stability': 1}]:
            with self.subTest(body=body), patch.object(self.app.state.scoring, 'score') as score:
                self.assertEqual(self.client.post('/api/v1/scoring/jobs', json=body).status_code, 422)
                score.assert_not_called()
        self.assertEqual(self.client.post('/api/v1/scoring/jobs', json={'identifier': REFERENCE['inn']},
            headers={'Origin': 'https://unrelated.test'}).status_code, 403)

    def test_session_result_contains_score_and_answer_change_clears_it(self):
        session = self.session()
        path = '/api/v1/credit/sessions/' + session['session_id']
        with patch.object(self.app.state.scoring, 'score', return_value=self.result):
            response = self.client.post(path + '/scoring/jobs', json={'revision': session['revision']})
            self.assertEqual(response.status_code, 202, response.text)
            result = self.wait(response.json())
        self.assertEqual(result['session_attachment']['status'], 'attached')
        saved = self.client.get(path + '/result').json()
        self.assertEqual(saved['scoring']['prediction']['predicted_class'], 1)
        self.assertFalse(saved['recommendation']['is_bank_decision'])
        self.assertEqual(saved['revision'], session['revision'] + 2)
        changed = self.client.post(path + '/answers', json={
            'revision': saved['revision'], 'question_id': 'amount', 'value': 2000000}).json()
        self.assertIsNone(changed['scoring'])
        self.assertIsNone(changed['scoring_job_id'])

    def test_concurrent_edit_does_not_attach_stale_result(self):
        session = self.session()
        path = '/api/v1/credit/sessions/' + session['session_id']
        entered, release = Event(), Event()
        def slow(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return deepcopy(self.result)
        with patch.object(self.app.state.scoring, 'score', side_effect=slow):
            job = self.client.post(path + '/scoring/jobs', json={'revision': session['revision']}).json()
            self.assertTrue(entered.wait(2))
            changed = self.client.post(path + '/answers', json={
                'revision': job['session_revision'], 'question_id': 'amount', 'value': 2000000})
            self.assertEqual(changed.status_code, 200)
            release.set()
            result = self.wait(job)
        self.assertEqual(result['session_attachment']['status'], 'stale_or_deleted_session')
        self.assertIsNone(self.client.get(path).json()['scoring'])

    def test_session_revision_conflict_does_not_start_a_job(self):
        session = self.session()
        with patch.object(self.app.state.jobs, 'start') as start:
            response = self.client.post('/api/v1/credit/sessions/' + session['session_id'] + '/scoring/jobs',
                                       json={'revision': 0})
            self.assertEqual(response.status_code, 409)
            start.assert_not_called()
