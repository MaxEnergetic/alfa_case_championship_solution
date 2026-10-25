"""Reproduce held-out pilot predictions from the delivered CSV."""
import argparse,json
from pathlib import Path
import joblib,pandas as pd
from model_code import make_features

p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--output',type=Path,default=Path('predictions.json'));a=p.parse_args()
base=Path(__file__).resolve().parent
frame=pd.read_csv(a.input,dtype=str,keep_default_na=False)
split=json.loads((base/'split_map.json').read_text())
frame=frame.loc[frame['meta_inn'].map(split)=='test'].copy()
assert len(frame)>0,'No held-out INNs found in input file'
assert (frame['pit_features_as_of']=='2025-01-11').all()
model=joblib.load(base/'model.joblib');threshold=json.loads((base/'metrics.json').read_text())['threshold']
prob=model.predict_proba(make_features(frame))[:,1]
result=[{'inn':inn,'probability_financial_stability_rule_1':float(v),'predicted_financial_class':int(v>=threshold),'features_as_of':'2025-01-11','outcome_year':2025} for inn,v in zip(frame['meta_inn'],prob)]
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(f'Saved {len(result)} pilot predictions; these are not lending decisions.')
