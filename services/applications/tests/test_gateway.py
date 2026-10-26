"""Real gateway and questionnaire contract; network fixtures are confined to tests."""
import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'services' / 'applications'))
import httpx
from fastapi.testclient import TestClient
from fns_parser.api import ApiSettings, create_app
from fns_parser.manual_captcha import CaptchaImage
from tests.test_credit import BASE, registry_fixture

with TemporaryDirectory() as initial, patch.dict(os.environ, {'DB_PATH': str(Path(initial) / 'accounts.db')}):
    import gateway


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.store = gateway.Accounts(str(Path(self.temp) / 'accounts.db'))
        self.enterContext(patch.object(gateway, 'accounts', self.store))
        self.core = create_app(ApiSettings(credit_db=Path(self.temp) / 'credit.db',
            captcha_db=Path(self.temp) / 'captcha.db', download_dir=Path(self.temp) / 'extracts', captcha_timeout=5))
        self.enterContext(TestClient(self.core))
        self.client = self.enterContext(TestClient(gateway.app))
        self.upstream = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.core), base_url='http://parser')
        gateway.app.state.parser = self.upstream
        self.addCleanup(lambda: asyncio.run(self.upstream.aclose()))
        self.auth = self.login('user@local.test', 'local-change-me')
        with self.store.db() as db:
            db.execute('INSERT INTO accounts VALUES (?,?,?)', ('other@local.test', 'salt', self.store.hash('other-pass', 'salt')))
        self.other = self.login('other@local.test', 'other-pass')

    def login(self, email, password):
        response = self.client.post('/api/v1/auth/login', json={'email': email, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return {'Authorization': 'Bearer ' + response.json()['token']}

    def create(self):
        r = self.client.post('/api/v1/credit/sessions', headers=self.auth)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def post(self, sid, suffix, value):
        return self.client.post('/api/v1/credit/sessions/' + sid + suffix, headers=self.auth, json=value)

    def wait(self, jid, state):
        for _ in range(100):
            response = self.client.get('/api/v1/jobs/' + jid, headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
            if response.json()['state'] == state:
                return response.json()
            time.sleep(.02)
        self.fail(response.text)

    def test_auth_ownership_and_no_banking_routes(self):
        self.assertEqual(self.client.get('/api/v1/credit/sessions').status_code, 401)
        session = self.create()
        path = '/api/v1/credit/sessions/' + session['session_id']
        self.assertEqual(self.client.get(path, headers=self.other).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/credit/sessions', headers=self.other).json(), [])
        for action in ('submit', 'sign', 'disburse', 'decision'):
            self.assertEqual(self.client.post(path + '/' + action, headers=self.auth).status_code, 404)
        self.assertEqual(self.client.post('/api/v1/auth/logout', headers=self.auth).status_code, 204)
        self.assertEqual(self.client.get(path, headers=self.auth).status_code, 401)

    def test_questionnaire_uses_core_rules_history_and_revision(self):
        x = self.create()
        sid = x['session_id']
        for _ in range(80):
            q = x['next_question']
            if not q:
                break
            response = self.post(sid, '/answers', {'revision': x['revision'], 'question_id': q['id'], 'value': BASE[q['id']]})
            self.assertEqual(response.status_code, 200, response.text)
            x = response.json()
        self.assertEqual(x['state'], 'ready')
        self.assertEqual(x['recommendation']['status'], 'R02')  # Registry not yet verified.
        self.assertFalse(x['recommendation']['is_bank_decision'])
        self.assertIsNone(x['scoring'])
        history = {r['question']['id']: r['value'] for r in x['answered_questions']}
        self.assertEqual(history['needed_date'], BASE['needed_date'])
        self.assertEqual(history['inn'], BASE['inn'])
        self.assertEqual(self.post(sid, '/answers', {'revision': 0, 'question_id': 'goal', 'value': 'vehicle'}).status_code, 409)
        changed = self.post(sid, '/answers', {'revision': x['revision'], 'question_id': 'goal', 'value': 'vehicle'}).json()
        self.assertIsNone(changed['recommendation'])
        self.assertNotIn('inn', [r['question']['id'] for r in changed['answered_questions']])

    def test_real_job_contract_and_partial_sources_are_preserved(self):
        x = self.create()
        data = registry_fixture()
        data['status'] = 'partial'
        data['sources']['transparent_business'] = {'status': 'error', 'error': {'code':'timeout', 'message':'Unavailable'}}
        with patch('fns_parser.api.parse_company', return_value=data) as parse:
            r = self.post(x['session_id'], '/registry/jobs', {'identifier': BASE['inn'], 'full': False, 'enrich': True})
            self.assertEqual(r.status_code, 202, r.text)
            job = self.wait(r.json()['job_id'], 'completed')
            parse.assert_called_once()
        self.assertEqual(job['result']['status'], 'partial')
        self.assertEqual(job['result']['sources']['transparent_business']['error']['code'], 'timeout')
        jid = job['job_id']
        self.assertEqual(self.client.get('/api/v1/jobs/'+jid, headers=self.other).status_code, 404)
        other_session = self.client.post('/api/v1/credit/sessions', headers=self.other).json()
        r = self.client.post('/api/v1/credit/sessions/'+other_session['session_id']+'/registry', headers=self.other,
            json={'revision':0,'job_id':jid})
        self.assertEqual(r.status_code, 404)

    def test_captcha_image_answer_and_same_job_resume(self):
        x = self.create()
        def runner(identifier, **kwargs):
            action, code = kwargs['captcha_handler'](image=CaptchaImage(b'test-image', 'image/png'),
                source='egrul', inn=identifier, source_url='https://egrul.nalog.ru/', attempt=1)
            self.assertEqual((action, code), ('answer', '123456'))
            return registry_fixture()
        with patch('fns_parser.api.parse_company', side_effect=runner):
            job = self.post(x['session_id'], '/registry/jobs', {'identifier':BASE['inn']}).json()
            waiting = self.wait(job['job_id'], 'waiting_for_captcha')
            cap = waiting['captcha']
            self.assertEqual(self.client.get(cap['image_url'], headers=self.other).status_code,404)
            image = self.client.get(cap['image_url'], headers=self.auth)
            self.assertEqual(image.content, b'test-image')
            self.assertEqual(image.headers['content-type'], 'image/png')
            response = self.client.post(cap['answer_url'], headers=self.auth, json={'action':'answer','code':'123456'})
            self.assertEqual(response.status_code, 200, response.text)
            done = self.wait(job['job_id'], 'completed')
            self.assertEqual(done['result']['entities'][0]['inn'], BASE['inn'])
            self.assertNotIn('captcha', done)

    def test_network_failure_is_503_not_fake_company(self):
        x = self.create()
        with patch.object(self.upstream, 'request', side_effect=httpx.ConnectError('offline')):
            r = self.client.get('/api/v1/credit/sessions/'+x['session_id'], headers=self.auth)
        self.assertEqual(r.status_code,503)
        self.assertNotIn('entities', r.json())

    def test_links_and_logins_survive_store_restart(self):
        x = self.create()
        with patch.object(gateway, 'accounts', gateway.Accounts(self.store.path)):
            r = self.client.get('/api/v1/credit/sessions/'+x['session_id'], headers=self.auth)
            self.assertEqual(r.status_code,200)
            self.assertEqual(len(self.client.get('/api/v1/credit/sessions',headers=self.auth).json()),1)


if __name__ == '__main__':
    unittest.main()
