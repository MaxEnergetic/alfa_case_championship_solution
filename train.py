"""Train a reproducible pilot. Input: labelled dataset_raw.csv or internal staging JSON."""
import argparse,json,sys,hashlib,warnings
from pathlib import Path
import numpy as np
import pandas as pd
import sklearn,joblib
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler,OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split,StratifiedKFold,GridSearchCV,cross_val_predict
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss,log_loss,balanced_accuracy_score,precision_score,recall_score,f1_score,confusion_matrix,accuracy_score
from sklearn.exceptions import ConvergenceWarning
from model_code import FEATURES,NUMERIC_FEATURES,CATEGORICAL_FEATURES,make_features,QuantileClipper

warnings.filterwarnings('error',category=ConvergenceWarning)
SEED=42

def metrics(y,p,threshold):
    pred=(p>=threshold).astype(int)
    return {'n':int(len(y)),'positive':int(np.sum(y)),'negative':int(len(y)-np.sum(y)),
     'roc_auc':float(roc_auc_score(y,p)),'average_precision_positive':float(average_precision_score(y,p)),
     'average_precision_negative':float(average_precision_score(1-np.asarray(y),1-p)),
     'brier_score':float(brier_score_loss(y,p)),'log_loss':float(log_loss(y,p,labels=[0,1])),
     'threshold':float(threshold),'accuracy':float(accuracy_score(y,pred)),
     'balanced_accuracy':float(balanced_accuracy_score(y,pred)),
     'precision_positive':float(precision_score(y,pred,zero_division=0)),
     'recall_positive':float(recall_score(y,pred,zero_division=0)),
     'recall_negative':float(recall_score(y,pred,pos_label=0,zero_division=0)),
     'f1_positive':float(f1_score(y,pred,zero_division=0)),
     'confusion_matrix_true_rows_predicted_columns_0_1':confusion_matrix(y,pred,labels=[0,1]).tolist()}

def load(path):
    if path.suffix=='.json':
        d=json.loads(path.read_text());return pd.DataFrame(d['rows'],columns=d['columns'])
    return pd.read_csv(path,dtype=str,keep_default_na=False)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True,type=Path);parser.add_argument('--output',type=Path,default=Path('.'));args=parser.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    raw=load(args.input)
    df=raw.loc[(raw['financial_stability_training_eligible']=='1') & (raw['target_financial_stability_missing']=='0')].copy().sort_values('meta_inn').reset_index(drop=True)
    assert df['meta_inn'].is_unique
    assert set(df['stability_label_rule_version'])=={'financial_stability_future_v1'}
    assert set(df['stability_label_features_as_of'])=={'2025-01-11'}
    assert set(df['stability_outcome_period_end'])=={'2025-12-31'}
    X=make_features(df);y=df['target_financial_stability'].astype(int)
    assert y.value_counts().min()>=50,'Too few cases for requested pilot validation'
    assert not any(c.startswith(('outcome_','target_')) for c in FEATURES)
    train,test=train_test_split(np.arange(len(df)),test_size=.2,random_state=SEED,stratify=y)
    assert set(df.iloc[train]['meta_inn']).isdisjoint(set(df.iloc[test]['meta_inn']))
    numeric=Pipeline([('clip',QuantileClipper()),('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True)),('scale',StandardScaler())])
    prep=ColumnTransformer([('numeric',numeric,NUMERIC_FEATURES),('sector',OneHotEncoder(handle_unknown='ignore'),CATEGORICAL_FEATURES)])
    pipe=Pipeline([('preprocess',prep),('classifier',LogisticRegression(solver='lbfgs',max_iter=3000,random_state=SEED))])
    cv=StratifiedKFold(n_splits=5,shuffle=True,random_state=SEED)
    search=GridSearchCV(pipe,{'classifier__C':[0.01,0.1,1.0,10.0]},scoring='neg_log_loss',cv=cv,n_jobs=1,error_score='raise',refit=True)
    search.fit(X.iloc[train],y.iloc[train]);model=search.best_estimator_
    print('selected C',search.best_params_,flush=True)
    # OOF predictions only from the development set; holdout is untouched.
    oof=cross_val_predict(model,X.iloc[train],y.iloc[train],cv=cv,method='predict_proba',n_jobs=1)[:,1]
    thresholds=np.linspace(.05,.95,181)
    threshold=max(thresholds,key=lambda t:(balanced_accuracy_score(y.iloc[train],oof>=t),-abs(t-.5)))
    p=model.predict_proba(X.iloc[test])[:,1]
    result={'model':'regularized logistic regression','seed':SEED,'train_n':len(train),'test_n':len(test),
      'target':'future financial stability proxy; not financing approval or probability of loan repayment',
      'positive_definition':'2025 equity>0 and net_profit>0 and current_assets>=current_liabilities',
      'negative_definition':'2025 equity<0 OR(net_profit<0 and current_assets<current_liabilities)',
      'unlabelled_definition':'intermediate, missing, non-filed or rejected quality; never treated as negative',
      'feature_cutoff':'2025-01-11','feature_financial_year':2023,'outcome_financial_year':2025,
      'validation':'20% company-disjoint stratified random holdout within one historical cohort; NOT out-of-time validation',
      'selection':'5-fold CV within development set; C selected by log loss; threshold by development OOF balanced accuracy',
      'selection_caveat':'OOF threshold predictions use C already selected on development data; final assessment uses untouched holdout',
      'best_C':search.best_params_['classifier__C'],'threshold':float(threshold),
      'threshold_meaning':'experimental balanced-accuracy classification threshold; not a validated credit approval cutoff',
      'cv_mean_log_loss_by_C':[{'C':v['classifier__C'],'log_loss':float(-s)} for v,s in zip(search.cv_results_['params'],search.cv_results_['mean_test_score'])],
      'test':metrics(y.iloc[test],p,threshold),'test_at_0_5':metrics(y.iloc[test],p,.5),
      'prior_baseline':metrics(y.iloc[test],np.full(len(test),float(y.iloc[train].mean())),.5),
      'limitations':['Labels are an operational financial rule, not expert truth or actual lending outcomes.','Only companies with observed, accepted future reports and decisive rule labels are evaluated; results do not establish quality for ambiguous/non-filing companies.','One cohort only; temporal generalization untested.','Original source selection and matched control additions are not a representative lending population.','RFSD includes accounting adjustments; quality flags and balance checks are retained.','Questionnaire data are absent; no assessment of requested amount, term or collateral.'],
      'versions':{'python':sys.version.split()[0],'sklearn':sklearn.__version__,'numpy':np.__version__,'pandas':pd.__version__},
      'feature_names':FEATURES,'model_refitted_on_holdout':False}
    # Compare a simple persistence rule on the subset where 2023 state is decisive.
    cur=df.iloc[test];vals=[]
    for _,r in cur.iterrows():
      def v(c):return float(r[c]) if r[c+'_missing']=='0' else None
      eq=v('pit_ras_2023_1300_equity_rub');pr=v('pit_ras_2023_2400_net_profit_rub');ca=v('pit_ras_2023_1200_current_assets_rub');cl=v('pit_ras_2023_1500_short_term_liabilities_rub')
      pred=None
      if None not in [eq,pr,ca,cl]:
        if eq<0 or(pr<0 and ca<cl):pred=0
        elif eq>0 and pr>0 and ca>=cl:pred=1
      vals.append(pred)
    covered=np.array([v is not None for v in vals]);actual=y.iloc[test].to_numpy()[covered];baseline=np.array([v for v in vals if v is not None],dtype=int)
    result['historical_state_persistence_baseline']={'coverage':float(covered.mean()),'n':int(covered.sum()),'accuracy':float(accuracy_score(actual,baseline)),'balanced_accuracy':float(balanced_accuracy_score(actual,baseline)),
      'model_accuracy_same_subset':float(accuracy_score(actual,p[covered]>=threshold)),'model_balanced_accuracy_same_subset':float(balanced_accuracy_score(actual,p[covered]>=threshold))}
    sector_metrics={}
    for sector in sorted(df.iloc[test]['pit_okved_section'].unique()):
      mask=(df.iloc[test]['pit_okved_section']==sector).to_numpy();yy=y.iloc[test].to_numpy()[mask]
      if len(yy)>=20 and len(np.unique(yy))==2:sector_metrics[sector]=metrics(yy,p[mask],threshold)
    result['test_by_sector']=sector_metrics
    names=model.named_steps['preprocess'].get_feature_names_out()
    coefficients=[{'transformed_feature':n,'coefficient':float(v)} for n,v in zip(names,model.named_steps['classifier'].coef_[0])]
    coefficients.sort(key=lambda r:abs(r['coefficient']),reverse=True)
    weights={'intercept':float(model.named_steps['classifier'].intercept_[0]),'coefficients':coefficients,
      'interpretation':'Coefficients multiply preprocessed features, not raw RUB amounts or arbitrary percentage shares; positive increases log odds of label 1. Correlated coefficients are not causal importance.',
      'numeric_feature_order':NUMERIC_FEATURES,
      'clip_lower_bounds':model.named_steps['preprocess'].named_transformers_['numeric'].named_steps['clip'].lower_bounds_.tolist(),
      'clip_upper_bounds':model.named_steps['preprocess'].named_transformers_['numeric'].named_steps['clip'].upper_bounds_.tolist(),
      'scaler_means':model.named_steps['preprocess'].named_transformers_['numeric'].named_steps['scale'].mean_.tolist(),
      'scaler_scales':model.named_steps['preprocess'].named_transformers_['numeric'].named_steps['scale'].scale_.tolist()}
    model_path=out/'model.joblib';joblib.dump(model,model_path)
    reloaded=joblib.load(model_path);assert np.allclose(reloaded.predict_proba(X.iloc[test])[:,1],p,rtol=0,atol=1e-12)
    # Shuffling every future-value column must leave model inputs/predictions unchanged.
    shuffled=df.iloc[test].copy()
    for col in shuffled.columns:
      if col.startswith('outcome_'):shuffled[col]=shuffled[col].iloc[::-1].to_numpy()
    assert make_features(shuffled).equals(X.iloc[test])
    assert np.allclose(model.predict_proba(make_features(shuffled))[:,1],p)
    split={str(df.iloc[i]['meta_inn']):'train' for i in train};split.update({str(df.iloc[i]['meta_inn']):'test' for i in test})
    predictions=[{'inn':str(df.iloc[i]['meta_inn']),'actual':int(y.iloc[i]),'probability_label_1':float(pp),'prediction':int(pp>=threshold)} for i,pp in zip(test,p)]
    for name,data in [('metrics.json',result),('weights.json',weights),('split_map.json',split),('holdout_predictions.json',predictions)]:
      (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'train':len(train),'test':len(test),'test_metrics':result['test'],'baseline':result['historical_state_persistence_baseline']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
