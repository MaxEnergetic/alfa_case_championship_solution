"""Synthetic images/answers only: never submit guessed answers to FNS."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from fns_parser.api import ApiSettings, create_app
from fns_parser.batch_training import collect, load_requests, read_json
from fns_parser.captcha_queue import CaptchaQueue, QueueCaptchaHandler
from fns_parser.fns_captcha import FnsRequests, SOURCES, challenge_form, raster_image
from fns_parser.providers import EgrulClient, TransparentBusinessClient
from fns_parser.transport import SourceError, check_payload, poll
from tests.test_training_batch import COMPANIES, fixture

TOKEN = 'A' * 40
READY = 'B' * 40
FORM = f'''<form id="frmCaptcha" action="captcha-proc.json" method="post">
<input name="captcha"><input name="captchaToken" value="{TOKEN}" type="hidden">
<img src="/static/captcha.bin?a={TOKEN}&amp;version=4"></form>'''.encode()


def image_bytes():
    out = BytesIO(); Image.new('RGB', (100, 30), 'white').save(out, 'PNG')
    return out.getvalue()


def session_for(*responses):
    session = Mock()
    session.json.side_effect = responses
    session.fetch.side_effect = lambda path, **kwargs: FORM if path == 'captcha-dialog.html' else image_bytes()
    return session


class FnsProtocolTests(unittest.TestCase):
    def test_pb_resends_exact_operation_with_token_and_original_session(self):
        session = session_for(SourceError('captcha_required', 'test'), READY, {'vyp': {'ОГРН': COMPANIES[0]['ogrn']}})
        human = Mock(return_value=('answer', '123456'))
        client = TransparentBusinessClient(session=session, captcha_handler=human)
        # Test the provider wiring as well as the protocol helper.
        session.json.side_effect = [SourceError('captcha_required','test'), READY,
                                    {'id':'request', 'token':'company'}, {'vyp': {'ОГРН':COMPANIES[0]['ogrn']}}]
        result = client.card({**COMPANIES[0], 'token':'original'})
        self.assertEqual(result['vyp']['ОГРН'], COMPANIES[0]['ogrn'])
        calls = session.json.call_args_list
        self.assertEqual(calls[0].args, ('company-proc.json', {'token':'original','method':'get-request'}))
        self.assertEqual(calls[1].args, ('captcha-proc.json', {'captcha':'123456','captchaToken':TOKEN}))
        self.assertEqual(calls[2].args, ('company-proc.json', {'token':'original','method':'get-request','pbCaptchaToken':READY}))
        self.assertEqual(human.call_args.kwargs['source'], 'transparent_business')
        self.assertEqual(human.call_args.kwargs['inn'], COMPANIES[0]['inn'])
        self.assertNotIn('123456', json.dumps(result))

    def test_egrul_pdf_token_goes_in_r_and_search_in_form_field(self):
        session = session_for(SourceError('captcha_required','test'), READY, {'status':'accepted'}, {'status':'ready'})
        session.fetch.side_effect = lambda path, **kwargs: FORM if path == 'captcha-dialog.html' else b'%PDF-test' if path.startswith('vyp-download/') else image_bytes()
        human = Mock(return_value=('answer', '123456'))
        client = EgrulClient(session=session, captcha_handler=human)
        self.assertEqual(client.download({'o':COMPANIES[0]['ogrn'], 'i':COMPANIES[0]['inn'], 't':'extract'}), b'%PDF-test')
        self.assertEqual(session.json.call_args_list[2].args, ('vyp-request/extract?r='+READY, None))
        session = session_for(SourceError('captcha_required','test'), READY, {'t':'job'})
        client = FnsRequests(session,'egrul',human)
        client.json('', {'query':COMPANIES[0]['ogrn']})
        self.assertEqual(session.json.call_args.args[1], {'query':COMPANIES[0]['ogrn'],'vyp3CaptchaToken':READY})

    def test_incorrect_answer_and_refresh_request_new_images_without_guesses(self):
        session = session_for(SourceError('captcha_required','test'), SourceError('captcha_required','wrong'), READY, {'id':'ok'})
        human = Mock(side_effect=[('answer','111111'),('refresh',None),('answer','222222')])
        client = FnsRequests(session,'egrul',human)
        result = client.json('', {'query':'4028051108'})
        self.assertEqual(result, {'id':'ok'})
        self.assertEqual([x.kwargs['attempt'] for x in human.call_args_list], [1,2,3])
        submissions = [x.args[1]['captcha'] for x in session.json.call_args_list if x.args[0]=='captcha-proc.json']
        self.assertEqual(submissions, ['111111','222222'])

    def test_no_handler_skip_and_bounded_refresh(self):
        for handler, expected in [(None,'captcha_required'), (Mock(return_value=('skip',None)), 'captcha_skipped'),
                                   (Mock(return_value=('refresh',None)), 'captcha_required')]:
            session = session_for(SourceError('captcha_required','test'))
            with self.assertRaises(SourceError) as cm:
                FnsRequests(session,'egrul',handler).json('', {'query':'4028051108'})
            self.assertEqual(cm.exception.code, expected)
            self.assertEqual(session.json.call_count,1)
            if handler: self.assertLessEqual(handler.call_count,5)

    def test_form_image_binding_and_raster_validation(self):
        self.assertEqual(challenge_form(FORM)[0], TOKEN)
        for raw in (FORM.replace(b'/static/captcha.bin',b'https://evil.test/static/captcha.bin'),
                    FORM.replace(b'captcha-proc.json',b'elsewhere'), FORM.replace(b'&amp;version=4',b'' ).replace(('a='+TOKEN).encode(),b'a=wrong')):
            with self.assertRaises(SourceError): challenge_form(raw)
        with self.assertRaises(SourceError): raster_image(b'<svg/>')

    def test_captcha_hint_retains_accepted_request_and_wait_budget_pauses(self):
        self.assertEqual(check_payload({'captchaRequired':True,'t':'accepted'})['t'],'accepted')
        with self.assertRaises(SourceError): check_payload({'captchaRequired':True})
        with self.assertRaises(SourceError): check_payload({'t':'accepted','ERRORS':{'captchaSearch':['required']}})
        clock = [0]; paused = [0]; count = [0]
        def fetch():
            count[0] += 1
            if count[0] == 1: clock[0] += 1000; paused[0] += 1000
            return count[0] == 2
        with patch('fns_parser.transport.time.monotonic',side_effect=lambda: clock[0]), patch('fns_parser.transport.time.sleep'):
            self.assertTrue(poll(fetch,bool,wait_timeout=1,pause_clock=lambda:paused[0]))


class BatchHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp)
        self.queue = CaptchaQueue(self.path/'captcha.sqlite3')
        self.app = create_app(ApiSettings(captcha_db=self.queue.path, credit_db=self.path/'credit.sqlite3'))
        self.client = self.enterContext(TestClient(self.app))

    def pending(self):
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            data = self.client.get('/api/v1/batch/captchas').json()['captchas']
            if data: return data[0]
            time.sleep(.01)
        self.fail('No pending captcha')

    def test_batch_blocks_same_company_then_continues_and_preserves_cache(self):
        # Two independent DB instances represent server/collector processes.
        out = self.path/'output'; requests=load_requests_for_test(self.path)
        collect(requests[:1],out,parser_func=lambda key,**kw:fixture(),sleep=lambda s:None)
        # Expand only this synthetic test input, never the production cohort.
        (out/'input.json').write_text(json.dumps(requests),encoding='utf8')
        visited=[]
        def parser(key, **kwargs):
            visited.append(key)
            company = next(c for c in COMPANIES if c['ogrn']==key)
            if key == COMPANIES[1]['ogrn']:
                session=session_for(SourceError('captcha_required','test'),READY,{'id':'ok'})
                req=FnsRequests(session,'transparent_business',kwargs['captcha_handler']);req.identifier=company['inn']
                req.json('company-proc.json',{'token':'original','method':'get-request'})
            return fixture(company)
        with ThreadPoolExecutor() as pool:
            task=pool.submit(collect, requests,out,parser_func=parser,captcha_queue=self.queue,captcha_timeout=3,sleep=lambda s:None)
            challenge=self.pending()
            self.assertFalse(task.done())
            self.assertEqual(visited,[COMPANIES[1]['ogrn']])
            self.assertEqual(read_json(out/'live.json')['state'],'waiting_for_captcha')
            self.assertEqual(self.client.get(challenge['image_url']).headers['content-type'],'image/png')
            status=self.client.post(challenge['answer_url'],json={'code':'123456'})
            self.assertEqual(status.status_code,200)
            run=task.result(timeout=4)
        self.assertIsNone(run['stopped_on'])
        self.assertEqual(run['cached_companies'],1)
        self.assertEqual(visited,[c['ogrn'] for c in COMPANIES[1:]])
        self.assertEqual(read_json(out/'records'/(COMPANIES[0]['ogrn']+'.json'))['attempt_count'],1)
        self.assertEqual(len(list((out/'records').glob('*.json'))),3)
        self.assertEqual(self.client.get(challenge['image_url']).status_code,409)
        self.assertEqual(self.client.post(challenge['answer_url'],json={'code':'123456'}).status_code,409)
        with self.queue.connect() as db:
            row=db.execute('SELECT * FROM challenges').fetchone()
            self.assertIsNone(row['answer']);self.assertIsNone(row['image'])

    def test_api_recreation_keeps_queue_and_rejects_cross_origin_and_wrong_code(self):
        challenge=dict(image=raster_image(image_bytes()),source='egrul',inn=COMPANIES[0]['inn'],source_url=SOURCES['egrul'],attempt=1)
        key=self.queue.publish('worker',timeout=10,**challenge)
        second=create_app(ApiSettings(captcha_db=self.queue.path,credit_db=self.path/'credit.sqlite3'))
        with TestClient(second) as client:
            c=client.get('/api/v1/batch/captchas').json()['captchas'][0]
            self.assertEqual(c['id'],key)
            self.assertEqual(client.post(c['answer_url'],json={'code':'123456'},headers={'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(client.post(c['answer_url'],json={'code':'abcdef'}).status_code,422)
            self.assertEqual(client.post(c['answer_url'],json={'action':'refresh'}).status_code,200)
        self.assertEqual(self.queue.consume(key,'worker'),('refresh',None))

    def test_expired_or_dead_worker_cannot_receive_answers(self):
        for field in ('expires','heartbeat'):
            key=self.queue.publish('worker',timeout=10,image=raster_image(image_bytes()),source='egrul',inn=COMPANIES[0]['inn'],source_url=SOURCES['egrul'],attempt=1)
            with self.queue.connect() as db: db.execute('UPDATE challenges SET '+field+'=0 WHERE id=?',(key,))
            self.assertEqual(self.client.get('/api/v1/batch/captchas').json()['captchas'],[])
            self.assertEqual(self.client.post('/api/v1/batch/captchas/'+key+'/answer',json={'code':'123456'}).status_code,409)
            with self.assertRaises(SourceError): self.queue.consume(key,'worker')

    def test_timeout_and_skip_stop_without_processing_next_company(self):
        for action in ('timeout','skip'):
            out=self.path/action; requests=load_requests_for_test(self.path)
            def parser(key,**kwargs):
                try:
                    client=FnsRequests(session_for(SourceError('captcha_required','test')),'egrul',kwargs['captcha_handler'])
                    client.identifier=COMPANIES[0]['inn'];client.json('',{})
                except SourceError as exc: return fixture(status='partial',error=exc.code)
                self.fail('Unexpected success')
            with ThreadPoolExecutor() as pool:
                task=pool.submit(collect,requests,out,parser_func=parser,captcha_queue=self.queue,captcha_timeout=.3 if action=='timeout' else 3,sleep=lambda s:None)
                if action=='skip':
                    c=self.pending();self.client.post(c['answer_url'],json={'action':'skip'})
                run=task.result(timeout=4)
            self.assertEqual(run['stopped_on']['code'],'captcha_timeout' if action=='timeout' else 'captcha_skipped')
            self.assertEqual(len(list((out/'records').glob('*.json'))),1)


def load_requests_for_test(path):
    f=path/'input.json';f.write_text(json.dumps(COMPANIES),encoding='utf8')
    return load_requests(f)
