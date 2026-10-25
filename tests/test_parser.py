import copy,json,math,struct,tempfile,unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from alfa_financial_parser.core import build_features,validate_inn,okved_section,enrich_company
from alfa_financial_parser.rfsd import RfsdClient,SourceError
from alfa_financial_parser.parquet import plain,hybrid,snappy,column

def snapshot():
    return {'schema_version':'rfsd_snapshot_v1','inn':'5505223431','year':2023,
      'source':{'units':'thousand_RUB','available_by':'2025-01-10T18:19:52Z'},
      'row':{'inn':'5505223431','ogrn':'0','creation_date':'2010-01-01','dissolution_date':None,
        'okved':'46.90','okved_section':'G','filed':1,'financial':0,'imputed':0,'outlier':0,'articulated':1,'totals_adjustment':0,
        'line_1200':500,'line_1250':50,'line_1300':600,'line_1400':200,'line_1500':200,
        'line_1600':1000,'line_1700':1000,'line_2110':2000,'line_2200':200,'line_2330':-20,'line_2400':100}}

class FeaturesTest(unittest.TestCase):
    def test_complete_values_and_units(self):
        r=build_features(snapshot(),'2025-01-11');f=r['features']
        self.assertTrue(r['can_predict']);self.assertEqual(r['financials_rub']['assets'],1_000_000)
        self.assertEqual(f['pit_current_ratio'],2.5);self.assertEqual(f['pit_equity_to_assets'],.6)
        self.assertEqual(f['pit_net_margin'],.05);self.assertEqual(f['pit_roa'],.1)
        self.assertEqual(f['pit_cash_to_assets'],.05);self.assertEqual(f['stability_liabilities_to_assets'],.4)
        self.assertEqual(f['stability_operating_profit_to_interest'],10)
        self.assertEqual(f['pit_log1p_assets_rub'],math.log1p(1_000_000))
        self.assertEqual(r['feature_coverage']['known'],14)
    def test_real_zero_not_missing(self):
        s=snapshot();s['row']['line_1250']=0;r=build_features(s,'2025-01-11')
        self.assertEqual(r['features']['pit_cash_to_assets'],0);self.assertEqual(r['features']['pit_cash_to_assets_missing'],0)
    def test_optional_missing_flag(self):
        s=snapshot();s['row']['line_1250']=None;r=build_features(s,'2025-01-11')
        self.assertTrue(r['can_predict']);self.assertEqual(r['features']['pit_cash_to_assets'],0)
        self.assertEqual(r['features']['pit_cash_to_assets_missing'],1)
    def test_core_missing_blocks_prediction(self):
        s=snapshot();s['row']['line_1300']=None;r=build_features(s,'2025-01-11')
        self.assertFalse(r['can_predict']);self.assertIn('equity',r['missing_core_fields'])
    def test_zero_denominator_no_infinity(self):
        s=snapshot();s['row']['line_1500']=0;s['row']['line_2330']=0;r=build_features(s,'2025-01-11')
        self.assertEqual(r['features']['pit_current_ratio_missing'],1)
        self.assertEqual(r['features']['stability_operating_profit_to_interest_missing'],1)
        json.dumps(r,allow_nan=False)
    def test_future_source_version_rejected(self):
        s=snapshot();s['source']['available_by']='2026-01-01';r=build_features(s,'2025-01-11')
        self.assertFalse(r['can_predict']);self.assertIn('source_version_published_after_as_of',r['quality_issues'])
    def test_stale_report_rejected(self):
        r=build_features(snapshot(),'2026-10-25');self.assertFalse(r['can_predict'])
        self.assertIn('financial_report_too_old',r['quality_issues'])
    def test_balance_mismatch_rejected(self):
        s=snapshot();s['row']['line_1700']=1002;r=build_features(s,'2025-01-11')
        self.assertIn('balance_mismatch',r['quality_issues'])
    def test_negative_equity_is_a_feature_not_data_error(self):
        s=snapshot();s['row']['line_1300']=-10;r=build_features(s,'2025-01-11')
        self.assertTrue(r['can_predict']);self.assertEqual(r['features']['stability_negative_equity'],1)
    def test_nonfiled_and_imputed_rejected(self):
        for key,value in [('filed',0),('imputed',1),('outlier',1),('financial',1),('articulated',0)]:
            with self.subTest(key=key):
                s=snapshot();s['row'][key]=value;self.assertFalse(build_features(s,'2025-01-11')['can_predict'])
    def test_registry_fallback_and_no_capital_substitution(self):
        s=snapshot();s['row']['creation_date']=None;s['row']['line_1300']=None
        c={'checked_at':'2025-01-11','entities':[{'inn':s['inn'],'registered_on':'2010-01-01','capital_rub':99999,'financials':[{'income':99999,'expenses':1}]}]}
        r=build_features(s,'2025-01-11',c)
        self.assertIsNotNone(r['features']['pit_company_age_years']);self.assertIsNone(r['financials_rub']['equity'])
        self.assertFalse(r['can_predict'])
    def test_future_compact_not_used(self):
        s=snapshot();s['row']['creation_date']=None
        c={'checked_at':'2026-01-01','entities':[{'inn':s['inn'],'registered_on':'2010-01-01'}]}
        r=build_features(s,'2025-01-11',c);self.assertEqual(r['features']['pit_company_age_years_missing'],1)
    def test_compact_identity_mismatch(self):
        with self.assertRaises(ValueError):build_features(snapshot(),'2025-01-11',{'entities':[{'inn':'5256038700'}]})
    def test_dissolution_blocks(self):
        s=snapshot();s['row']['dissolution_date']='2024-12-01';self.assertFalse(build_features(s,'2025-01-11')['can_predict'])
    def test_unknown_company(self):
        s=snapshot();s['row']=None;r=build_features(s,'2025-01-11');self.assertFalse(r['can_predict']);self.assertIsNone(r['prediction'])
    def test_inn_validation(self):
        self.assertEqual(validate_inn('0572004524'),'0572004524')
        for inn in [5505223431,'5505223432','123','123456789012']:
            with self.subTest(inn=inn),self.assertRaises(ValueError):validate_inn(inn)
    def test_okved(self):
        self.assertEqual(okved_section('01.11'),'A');self.assertEqual(okved_section('46.90'),'G')
        self.assertEqual(okved_section('33.12'),'C');self.assertIsNone(okved_section('1970-01-01'))
    def test_source_unavailable_has_no_prediction(self):
        class Client:
            def fetch(self,*args,**kwargs):raise SourceError('HTTP 503')
        r=enrich_company('5505223431',client=Client());self.assertEqual(r['status'],'source_unavailable');self.assertIsNone(r['prediction'])

class TransportTest(unittest.TestCase):
    def test_invalid_json_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'response.json';client=RfsdClient()
            with patch.object(client,'_request',return_value=b'<html>Unavailable</html>'):
                with self.assertRaises(SourceError):client._json('https://example.invalid',p)
            self.assertFalse(p.exists())
    def test_does_not_accept_full_file_instead_of_range(self):
        class Response:
            status=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):raise AssertionError('A full file must not be read')
        with patch('alfa_financial_parser.rfsd.urlopen',return_value=Response()):
            with self.assertRaises(SourceError):RfsdClient()._request('https://example.invalid',limit=8,expected_range=(0,7))
    def test_offline_cannot_resolve_main(self):
        with self.assertRaises(SourceError):RfsdClient(offline=True).resolve_revision('main')
    def test_cached_range_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'range';p.write_bytes(b'bad')
            with self.assertRaises(SourceError):RfsdClient(offline=True)._range('x',0,7,p)
    def test_supported_decoders(self):
        self.assertEqual(plain(struct.pack('<2d',1.5,-2),5,2),[1.5,-2])
        self.assertEqual(hybrid(bytes([6,1]),1,3),[1,1,1])
        self.assertEqual(hybrid(bytes([3]),0,8),[0]*8)
        self.assertEqual(snappy(bytes([3,8])+b'abc'),b'abc')
    def test_unsupported_codec_fails(self):
        with self.assertRaises(ValueError):column(b'',{1:5,4:7,5:0})

if __name__=='__main__':unittest.main()
