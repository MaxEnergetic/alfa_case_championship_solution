"""Normalization, model-compatible features, data gates and optional prediction."""
import importlib,json,math,sys
from datetime import date
from pathlib import Path
from .rfsd import RfsdClient,SourceError

LINES={'1200':'current_assets','1250':'cash','1300':'equity','1400':'long_term_liabilities',
 '1500':'short_term_liabilities','1600':'assets','1700':'equity_and_liabilities',
 '2110':'revenue','2200':'operating_profit','2330':'interest_expense','2400':'net_profit'}
FEATURES=['pit_current_ratio','pit_equity_to_assets','pit_net_margin','pit_roa',
 'pit_cash_to_assets','pit_log1p_assets_rub','pit_log1p_revenue_rub','pit_company_age_years',
 'stability_liabilities_to_assets','stability_operating_profit_to_interest',
 'stability_negative_equity','stability_net_loss','stability_negative_working_capital','pit_okved_section']

def validate_inn(value):
    if not isinstance(value,str):raise ValueError('INN must be a string to preserve leading zeros')
    value=value.strip()
    if len(value)==12 and value.isascii() and value.isdigit():raise ValueError('Only 10-digit legal-entity INNs are supported; this model was not trained for individual entrepreneurs')
    if len(value)!=10 or not value.isascii() or not value.isdigit():raise ValueError('Expected a 10-digit legal-entity INN')
    check=sum(int(a)*b for a,b in zip(value[:9],[2,4,10,3,5,9,4,6,8]))%11%10
    if check!=int(value[9]):raise ValueError('INN checksum is invalid')
    return value

def numeric(value):
    if value is None:return None
    try:v=float(value)
    except (ValueError,TypeError):return None
    return v if math.isfinite(v) else None

def okved_section(value):
    """OKVED2 section, derived from a dotted two-digit division code."""
    if value is None:return None
    s=str(value).strip()
    if s in list('ABCDEFGHIJKLMNOPQRSTU'):return s
    division=s.split('.')[0]
    if not division.isdigit() or len(division)!=2:return None
    n=int(division)
    for low,high,section in [(1,3,'A'),(5,9,'B'),(10,33,'C'),(35,35,'D'),(36,39,'E'),(41,43,'F'),(45,47,'G'),(49,53,'H'),(55,56,'I'),(58,63,'J'),(64,66,'K'),(68,68,'L'),(69,75,'M'),(77,82,'N'),(84,84,'O'),(85,85,'P'),(86,88,'Q'),(90,93,'R'),(94,96,'S'),(97,98,'T'),(99,99,'U')]:
        if low<=n<=high:return section
    return None

def compact_entity(compact,inn,as_of):
    if compact is None:return None,[]
    if not isinstance(compact,dict):raise ValueError('compact JSON must be an object')
    entities=compact.get('entities',[])
    if not isinstance(entities,list):raise ValueError('compact.entities must be a list')
    matches=[e for e in entities if isinstance(e,dict) and e.get('inn')==inn]
    if len(matches)!=1:raise ValueError('compact JSON must contain exactly one entity matching the requested INN')
    checked=compact.get('checked_at')
    if not checked:return None,['compact_registry_ignored_without_observation_date']
    try:observed=date.fromisoformat(checked[:10])
    except (ValueError,TypeError):raise ValueError('Invalid compact.checked_at date')
    if observed>as_of:return None,['compact_registry_ignored_because_observed_after_as_of']
    return matches[0],[]

def build_features(snapshot,as_of=None,compact=None,max_report_age_days=730):
    as_of=date.fromisoformat(as_of) if isinstance(as_of,str) else (as_of or date.today())
    if max_report_age_days<=0:raise ValueError('max_report_age_days must be positive')
    inn=validate_inn(snapshot['inn']);source=snapshot.get('source',{});year=int(snapshot['year'])
    row=snapshot.get('row');period_end=date(year,12,31)
    result={'schema_version':'alfa_enrichment_v1','inn':inn,'features_as_of':as_of.isoformat(),
      'report_year':year,'report_period_end':period_end.isoformat(),'source':source,
      'financials_rub':{},'features':{},'status':'insufficient_data','can_predict':False,
      'missing_core_fields':[],'quality_issues':[],'warnings':[],'prediction':None}
    def issue(message):result['quality_issues'].append(message)
    if source.get('units')!='thousand_RUB':raise ValueError('This adapter requires RFSD amounts in thousand_RUB')
    try:available=date.fromisoformat(source['available_by'][:10])
    except (ValueError,KeyError,TypeError):available=None;issue('source_availability_date_missing')
    if available and available>as_of:issue('source_version_published_after_as_of')
    if period_end>=as_of:issue('report_period_not_before_as_of')
    age=(as_of-period_end).days;result['report_age_days']=age
    if age>max_report_age_days:issue('financial_report_too_old')
    if row is None:
        issue('company_not_found_in_selected_RFSD_year');return result
    if row.get('inn')!=inn:raise ValueError('Source INN mismatch')
    for code,name in LINES.items():
        v=numeric(row.get('line_'+code));result['financials_rub'][name]=None if v is None else v*1000
    result['quality_flags']={k:row.get(k) for k in ['filed','financial','imputed','outlier','articulated','totals_adjustment']}
    for k,expected in [('filed',1),('financial',0),('imputed',0),('outlier',0),('articulated',1)]:
        if numeric(row.get(k))!=expected:issue('source_quality_'+k)
    if row.get('totals_adjustment')==1:result['warnings'].append('source_contains_accounting_total_adjustments')
    created=row.get('creation_date');okved=row.get('okved');section=okved_section(row.get('okved_section')) or okved_section(okved)
    entity,warnings=compact_entity(compact,inn,as_of);result['warnings'].extend(warnings)
    if entity:
        # Only registry metadata can fill gaps. income/expenses/capital_rub are not RAS substitutes.
        if not created:created=entity.get('registered_on')
        elif entity.get('registered_on') and created!=entity['registered_on']:result['warnings'].append('registration_date_conflict_RFSD_value_retained')
        if not section:okved=entity.get('main_okved');section=okved_section(okved)
        result['company_name']=entity.get('name')
    company_age=None
    if created:
        try:created_date=date.fromisoformat(created)
        except (ValueError,TypeError):issue('invalid_registration_date')
        else:
            if created_date>as_of:issue('registration_date_after_as_of')
            else:company_age=(as_of-created_date).days/365.25
    else:issue('registration_date_missing')
    if not section:issue('okved_section_missing')
    dissolved=row.get('dissolution_date')
    if dissolved:
        try:
            if date.fromisoformat(dissolved)<=as_of:issue('company_dissolved_on_or_before_as_of')
        except (ValueError,TypeError):issue('invalid_dissolution_date')
    result['registry']={'ogrn':row.get('ogrn'),'registered_on':created,'main_okved':okved,'okved_section':section,'dissolution_date':dissolved}
    f=result['financials_rub'];v=lambda name:f.get(name)
    def put(key,value):
        if value is None or isinstance(value,(int,float)) and not math.isfinite(value):
            result['features'][key]=0;result['features'][key+'_missing']=1
        else:result['features'][key]=value;result['features'][key+'_missing']=0
    def ratio(a,b):
        x,y=v(a),v(b);return x/y if x is not None and y is not None and y>0 else None
    for key,a,b in [('pit_current_ratio','current_assets','short_term_liabilities'),('pit_equity_to_assets','equity','assets'),('pit_net_margin','net_profit','revenue'),('pit_roa','net_profit','assets'),('pit_cash_to_assets','cash','assets')]:put(key,ratio(a,b))
    for name in ['assets','revenue']:
        value=v(name);put('pit_log1p_'+name+'_rub',math.log1p(value) if value is not None and value>=0 else None)
    put('pit_company_age_years',company_age);put('pit_okved_section',section)
    liab,long,assets=v('short_term_liabilities'),v('long_term_liabilities'),v('assets')
    put('stability_liabilities_to_assets',(liab+long)/assets if None not in [liab,long,assets] and assets>0 else None)
    op,interest=v('operating_profit'),v('interest_expense')
    put('stability_operating_profit_to_interest',op/abs(interest) if op is not None and interest is not None and interest!=0 else None)
    eq,profit,ca,cl=v('equity'),v('net_profit'),v('current_assets'),v('short_term_liabilities')
    put('stability_negative_equity',int(eq<0) if eq is not None else None)
    put('stability_net_loss',int(profit<0) if profit is not None else None)
    put('stability_negative_working_capital',int(ca<cl) if ca is not None and cl is not None else None)
    required=['assets','equity_and_liabilities','current_assets','equity','short_term_liabilities','revenue','net_profit']
    result['missing_core_fields']=[k for k in required if v(k) is None]
    if result['missing_core_fields']:issue('core_financial_fields_missing')
    for name in ['assets','equity_and_liabilities','current_assets','short_term_liabilities','long_term_liabilities','cash','revenue']:
        if v(name) is not None and v(name)<0:issue('negative_'+name)
    if assets is not None and assets<=0:issue('nonpositive_assets')
    balance=v('equity_and_liabilities')
    result['balance_difference_rub']=assets-balance if assets is not None and balance is not None else None
    if result['balance_difference_rub'] is not None and abs(result['balance_difference_rub'])>1000:issue('balance_mismatch')
    known=sum(result['features'][name+'_missing']==0 for name in FEATURES)
    result['feature_coverage']={'known':known,'total':len(FEATURES),'fraction':known/len(FEATURES)}
    result['can_predict']=not result['quality_issues']
    result['status']='ready' if result['can_predict'] else 'insufficient_data'
    return result

def predict(result,model_dir):
    if not result['can_predict']:return result
    model_dir=Path(model_dir).resolve()
    for name in ['model.joblib','model_code.py','metrics.json']:
        if not (model_dir/name).is_file():raise ValueError('Missing model file: '+str(model_dir/name))
    try:import joblib;import pandas as pd
    except ImportError as e:raise ValueError('Prediction dependencies are missing: pip install -r requirements.txt') from e
    existing=sys.modules.get('model_code')
    if existing is not None and Path(existing.__file__).resolve()!=model_dir/'model_code.py':
        raise ValueError('Another model_code module is already imported; use a separate process or the matching model directory')
    sys.path.insert(0,str(model_dir))
    try:
        code=importlib.import_module('model_code')
        if list(code.FEATURES)!=FEATURES:raise ValueError('Model feature contract differs from this parser')
        model=joblib.load(model_dir/'model.joblib')
    finally:sys.path.pop(0)
    metrics=json.loads((model_dir/'metrics.json').read_text(encoding='utf-8'))
    if list(model.classes_)!=[0,1]:raise ValueError('Unexpected model class ordering')
    frame=pd.DataFrame([result['features']]);prob=float(model.predict_proba(code.make_features(frame))[0,1])
    threshold=float(metrics['threshold']);label=int(prob>=threshold)
    same_cohort=result['report_year']==metrics['feature_financial_year'] and result['features_as_of']==metrics['feature_cutoff']
    if not same_cohort:result['warnings'].append('model_not_validated_on_this_time_period')
    result['prediction']={'probability_financial_stability':prob,'probability_financial_distress':1-prob,
       'predicted_class':label,'threshold':threshold,
       'recommendation':'favorable_financial_profile' if label else 'financial_risk_review',
       'meaning':'historically trained financial-state proxy; not a loan repayment probability or lending decision',
       'time_period_validation':'same_cohort' if same_cohort else 'not_validated',
       'target_financial_year':metrics['outcome_financial_year'] if same_cohort else None,
       'training_feature_year':metrics['feature_financial_year'],'training_outcome_year':metrics['outcome_financial_year']}
    return result

def enrich_company(inn,year=None,as_of=None,compact=None,cache_dir='.cache/alfa_financial',revision='main',
                   model_dir=None,timeout=45,offline=False,max_report_age_days=730,client=None):
    """Public API. Source failures return no prediction and an explicit status."""
    inn=validate_inn(inn);as_of=date.fromisoformat(as_of) if isinstance(as_of,str) else (as_of or date.today())
    if compact is not None:compact_entity(compact,inn,as_of) # Validate identity before network access.
    client=client or RfsdClient(cache_dir=cache_dir,timeout=timeout,offline=offline)
    try:snapshot=client.fetch(inn,year=year,as_of=as_of,revision=revision)
    except SourceError as e:
        return {'schema_version':'alfa_enrichment_v1','inn':inn,'features_as_of':as_of.isoformat(),
          'status':'source_unavailable','can_predict':False,'error':str(e),'features':{},'prediction':None}
    result=build_features(snapshot,as_of,compact,max_report_age_days)
    if model_dir is not None:result=predict(result,model_dir)
    return result
