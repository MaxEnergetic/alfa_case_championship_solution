"""Windows/Linux CLI: INN -> RFSD statement -> model features -> optional score."""
import argparse,json,sys
from pathlib import Path
from alfa_financial_parser import enrich_company,SourceError
from alfa_financial_parser.rfsd import atomic_write

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inn',required=True,help='10-digit legal-entity INN')
    p.add_argument('--year',type=int,help='Report year; defaults to latest completed RFSD partition')
    p.add_argument('--as-of',help='Feature snapshot date YYYY-MM-DD; defaults to local current date')
    p.add_argument('--compact',type=Path,help='Existing company.compact.json; only registry gaps are filled from it')
    p.add_argument('--revision',default='main',help='main or exact RFSD commit SHA')
    p.add_argument('--cache-dir',type=Path,default=Path('.cache/alfa_financial'))
    p.add_argument('--offline',action='store_true',help='Use previously cached exact revision/year')
    p.add_argument('--timeout',type=float,default=45)
    p.add_argument('--max-report-age-days',type=int,default=730)
    p.add_argument('--model-dir',type=Path,default=Path(__file__).resolve().parent/'model')
    p.add_argument('--no-predict',action='store_true',help='Only enrich and calculate features; no ML dependencies needed')
    p.add_argument('--out',type=Path,help='JSON result path; stdout when omitted')
    a=p.parse_args(argv)
    try:
        compact=json.loads(a.compact.read_text(encoding='utf-8-sig')) if a.compact else None
        result=enrich_company(a.inn,year=a.year,as_of=a.as_of,compact=compact,cache_dir=a.cache_dir,
           revision=a.revision,model_dir=None if a.no_predict else a.model_dir,timeout=a.timeout,
           offline=a.offline,max_report_age_days=a.max_report_age_days)
    except (ValueError,SourceError,OSError,AssertionError,KeyError,EOFError) as e:
        result={'schema_version':'alfa_enrichment_v1','inn':a.inn,'status':'error','can_predict':False,'error':str(e),'prediction':None}
    data=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.out:atomic_write(a.out,data.encode('utf-8'));print(f'{result["status"]}: {a.out}',file=sys.stderr)
    else:print(data,end='')
    return 0 if result['status']=='ready' else 2 if result['status']=='insufficient_data' else 3

if __name__=='__main__':sys.exit(main())
