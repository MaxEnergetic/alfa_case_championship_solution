"""Grouped questions, legacy compatibility and honest combined recommendation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from fns_parser.api import ApiSettings, create_app
from fns_parser.credit.assessment import combined


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(TemporaryDirectory()))
        self.client = self.enterContext(TestClient(create_app(ApiSettings(
            credit_db=tmp/'credit.db', captcha_db=tmp/'captcha.db', download_dir=tmp/'extracts'))))

    def answer(self, x, value):
        r = self.client.post('/api/v1/credit/sessions/'+x['session_id']+'/answers', json={
            'revision': x['revision'], 'question_id': x['next_question']['id'], 'value': value})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def purchase(self, version=2):
        x = self.client.post('/api/v1/credit/sessions', json={'flow_version':version}).json()
        for value in ('no', 'property', 'yes'):
            x = self.answer(x, value)
        self.assertEqual(x['next_question']['id'], 'asset_cost')
        return x

    def test_purchase_and_credit_amount_saved_together_once(self):
        x = self.purchase()
        self.assertEqual([q['id'] for q in x['question_group']], ['asset_cost', 'amount'])
        url = '/api/v1/credit/sessions/'+x['session_id']
        body = {'revision':x['revision'],'answers':[
            {'question_id':'asset_cost','value':5000000},{'question_id':'amount','value':3000000}]}
        r = self.client.post(url+'/answers/batch',json=body)
        self.assertEqual(r.status_code,200,r.text)
        x = r.json()
        self.assertEqual(x['next_question']['id'],'registered')
        self.assertEqual(x['data']['financing']['asset_cost_rub'],5000000)
        self.assertEqual(x['data']['financing']['amount_rub'],3000000)
        # Continue through the company section: amount must not be asked again.
        for value in ('yes','ooo','5505223431','no'):
            x = self.answer(x,value)
        self.assertEqual(x['next_question']['id'],'needed_date')
        self.assertEqual(sum(r['question']['id']=='amount' for r in x['answered_questions']),1)
        self.assertEqual(self.client.post(url+'/answers/batch',json=body).status_code,409)

    def test_invalid_group_rolls_back_all_answers_and_revision(self):
        x = self.purchase()
        url = '/api/v1/credit/sessions/'+x['session_id']
        r = self.client.post(url+'/answers/batch',json={'revision':x['revision'],'answers':[
            {'question_id':'asset_cost','value':5000000},{'question_id':'amount','value':-1}]})
        self.assertEqual(r.status_code,422)
        self.assertEqual(self.client.get(url).json(),x)

    def test_saved_legacy_order_remains_readable(self):
        x = self.purchase(version=1)
        self.assertEqual(x['question_group'],[])
        x = self.answer(x,5000000)
        self.assertEqual(x['next_question']['id'],'registered')

    def test_combined_assessment_uses_risk_without_inventing_approval(self):
        recommendation={'status':'R01','issues':[], 'candidates':[
            {'code':'P08','name':'Business mortgage','status':'candidate','reason':'Purchase','checks':[]}]}
        score={'status':'ready','report_year':2025,'prediction':{
            'predicted_class':1,'probability_financial_stability':.83,'time_period_validation':'not_validated'}}
        result=combined(recommendation,score,'5505223431')
        self.assertEqual(result['status'],'favorable_profile')
        self.assertEqual(result['financial_assessment']['probability_financial_stability'],.83)
        self.assertIsNone(result['approval_probability']['value'])
        self.assertFalse(result['is_bank_decision'])
        score['prediction']['predicted_class']=0
        self.assertEqual(combined(recommendation,score,'5505223431')['products'][0]['readiness'],'risk_review')
        self.assertEqual(combined(recommendation,None,'784806113663')['financial_assessment']['status'],'not_applicable')
        self.assertIsNone(combined(None,score,'5505223431'))

    def test_missing_financials_never_look_like_zero_risk(self):
        recommendation={'status':'R02','issues':['Q13: Укажите ИНН.'], 'candidates':[
            {'code':'P08','name':'Business mortgage','status':'clarify','reason':'Purchase','checks':[]}]}
        result=combined(recommendation,{'status':'source_unavailable'},'5505223431')
        self.assertEqual(result['status'],'needs_information')
        self.assertIsNone(result['financial_assessment']['probability_financial_stability'])
        self.assertEqual(result['clarifications'],['Укажите ИНН.'])


if __name__=='__main__':
    unittest.main()
