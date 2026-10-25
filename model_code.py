"""Feature allowlist and train-only preprocessing for financial stability proxy."""
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator,TransformerMixin

NUMERIC_FEATURES=[
 'pit_current_ratio','pit_equity_to_assets','pit_net_margin','pit_roa',
 'pit_cash_to_assets','pit_log1p_assets_rub','pit_log1p_revenue_rub',
 'pit_company_age_years','stability_liabilities_to_assets',
 'stability_operating_profit_to_interest','stability_negative_equity',
 'stability_net_loss','stability_negative_working_capital']
CATEGORICAL_FEATURES=['pit_okved_section']
FEATURES=NUMERIC_FEATURES+CATEGORICAL_FEATURES

def make_features(frame):
    result=pd.DataFrame(index=frame.index)
    for col in NUMERIC_FEATURES:
        value=pd.to_numeric(frame[col],errors='coerce')
        result[col]=value.mask(frame[col+'_missing'].astype(str)!='0').replace([np.inf,-np.inf],np.nan).astype(float)
    for col in CATEGORICAL_FEATURES:
        result[col]=frame[col].astype(str).where(frame[col+'_missing'].astype(str)=='0','UNKNOWN')
    return result[FEATURES]

class QuantileClipper(TransformerMixin,BaseEstimator):
    """Learn clipping limits inside each training fold; preserve missing values."""
    def __init__(self,lower=0.01,upper=0.99):self.lower=lower;self.upper=upper
    def fit(self,X,y=None):
        a=np.asarray(X,dtype=float);self.n_features_in_=a.shape[1]
        self.lower_bounds_=np.array([np.nanquantile(a[:,i],self.lower) if np.isfinite(a[:,i]).any() else -np.inf for i in range(a.shape[1])])
        self.upper_bounds_=np.array([np.nanquantile(a[:,i],self.upper) if np.isfinite(a[:,i]).any() else np.inf for i in range(a.shape[1])])
        return self
    def transform(self,X):return np.clip(np.asarray(X,dtype=float),self.lower_bounds_,self.upper_bounds_)
    def get_feature_names_out(self,input_features=None):
        return np.asarray(input_features if input_features is not None else [f'x{i}' for i in range(self.n_features_in_)],dtype=object)
