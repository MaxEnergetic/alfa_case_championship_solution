"""Recompute the financial proxy label from the outcome fields in the delivered CSV."""
import math

def outcome_label(row):
    def number(key):
        if row.get(key+'_missing')!='0':return None
        try:v=float(row[key])
        except (ValueError,TypeError):return None
        return v if math.isfinite(v) else None
    if row.get('stability_snapshot_verified')!='1':return None
    if row.get('stability_label_features_as_of','9999')>=row.get('stability_outcome_period_end','0'):return None
    for key,expected in [('filed',1),('financial',0),('imputed',0),('outlier',0),('articulated',1)]:
        if number('outcome_rfsd_'+key)!=expected:return None
    assets=number('outcome_ras_2025_1600_assets_rub');balance=number('outcome_ras_2025_1700_equity_and_liabilities_rub')
    if assets is None or balance is None or min(assets,balance)<0 or abs(assets-balance)>1000:return None
    equity=number('outcome_ras_2025_1300_equity_rub');profit=number('outcome_ras_2025_2400_net_profit_rub')
    current_assets=number('outcome_ras_2025_1200_current_assets_rub');current_liabilities=number('outcome_ras_2025_1500_current_liabilities_rub')
    if None in [equity,profit,current_assets,current_liabilities]:return None
    if current_assets<0 or current_liabilities<0:return None
    if equity<0 or (profit<0 and current_assets<current_liabilities):return 0
    if equity>0 and profit>0 and current_assets>=current_liabilities:return 1
    return None

if __name__=='__main__':
    import argparse,csv,collections
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);a=p.parse_args()
    counts=collections.Counter()
    with open(a.input,encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            expected=outcome_label(row)
            actual=None if row['target_financial_stability_missing']=='1' else int(row['target_financial_stability'])
            assert expected==actual,(row['meta_inn'],expected,actual)
            counts[str(actual)]+=1
    print(dict(counts))
