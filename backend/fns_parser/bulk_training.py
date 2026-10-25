"""Build the whole scoring table from bulk FNS releases plus saved parser data."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from .batch_training import load_requests, read_json, result_path, run_lock
from .bulk_sources import RELEASES, fetch_all
from .dataset_pool import MSP_URL, FIN_URL
from .training import (atomic_json, compact_document, export_tables, normalize_request, project)


def bulk_entity(company, supplemental):
    entity = {'inn': company['inn'], 'ogrn': company['ogrn'], 'name': company.get('name'),
              'entity_type': 'legal', 'main_okved': company.get('main_okved'),
              'employees': {'year': company.get('employees_year'), 'count': company.get('employees')},
              'data_dates': {'employees': company.get('msp_as_of')}}
    provenance = {'msp': {'url': MSP_URL, 'member': company.get('msp_source_member'),
                          'record': company.get('msp_source_record'), 'as_of': company.get('msp_as_of')}}
    financials = {}
    fin = company.get('financials')
    if fin:
        year = int(fin['period_end'][:4])
        financials[year] = {'year': year, 'income': fin.get('income_rub'), 'expenses': fin.get('expenses_rub')}
        entity['data_dates']['financials'] = fin.get('document_date')
        provenance['income_expenses'] = {'url': FIN_URL, 'member': fin.get('source_member'),
                                         'record': fin.get('source_record'), 'period_end': fin['period_end'],
                                         'published_on': fin.get('document_date')}
    for name, release in supplemental.items():
        data = release.get('companies', {}).get(company['inn'])
        if data is None:
            continue  # Absence from a published list is not a zero or OSNO.
        provenance[name] = {k: v for k, v in data.items() if k in (
            'source_url', 'source_member', 'source_record', 'as_of', 'published_on')}
        if name == 'snr':
            entity['tax_regime'] = {'regimes': data['regimes'], 'period': data['as_of'][:7]}
        elif name == 'paytax':
            financials.setdefault(data['year'], {'year': data['year']})['taxes_paid'] = data['taxes_paid_rub']
            entity['data_dates']['taxes_paid'] = data['published_on']
        elif name == 'debtam':
            entity['tax_debt'] = {'status': 'available', 'amount': data['amount_rub'], 'period': data['as_of'][:7]}
            entity['data_dates']['tax_debt'] = data['published_on']
    entity['financials'] = sorted(financials.values(), key=lambda v: v['year'], reverse=True)
    return entity, provenance


def combine(company, supplemental, document=None):
    request = normalize_request(company)
    bulk, evidence = bulk_entity(company, supplemental)
    compact = compact_document(document) if document is not None else None
    if compact is not None:
        project(compact, request)  # Validate identity before joining sources.
    parsed = (compact or {}).get('entities', [])
    parsed = next((e for e in parsed if e.get('ogrn') == request['ogrn']), {})
    entity = deepcopy(parsed)
    owners = {}
    for key in ('inn', 'ogrn', 'name', 'entity_type', 'main_okved'):
        if entity.get(key) is None and bulk.get(key) is not None:
            entity[key] = bulk[key]
            owners[key] = 'msp'
    # Fill groups together so a count/amount is never paired with another period.
    if bulk['employees']['count'] is not None:
        entity['employees'] = bulk['employees']
        owners['employees'] = 'msp'
    dates = deepcopy(entity.get('data_dates', {}))
    for key in ('tax_regime', 'tax_debt'):
        if bulk.get(key):
            entity[key] = bulk[key]
            owners[key] = 'snr' if key == 'tax_regime' else 'debtam'
    for key, value in bulk['data_dates'].items():
        if value is not None:
            dates[key] = value
    entity['data_dates'] = dates
    financials = {v['year']: deepcopy(v) for v in parsed.get('financials', [])}
    financial_owners = {}
    for entry in bulk['financials']:
        year = entry['year']
        target = financials.setdefault(year, {'year': year})
        for key in ('income', 'expenses', 'taxes_paid'):
            if entry.get(key) is not None:
                target[key] = entry[key]
                financial_owners[(year, key)] = 'paytax' if key == 'taxes_paid' else 'income_expenses'
    entity['financials'] = sorted(financials.values(), key=lambda v: v['year'], reverse=True)
    result = {'schema_version': 'compact-1.0', 'query': request['identifier'],
              'status': 'bulk_enriched' if parsed else 'bulk_only',
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'sources': {**(compact or {}).get('sources', {}),
                          'fns_msp_bulk': {'status': 'ok'},
                          'fns_income_expenses_bulk': {'status': 'ok' if company.get('financials') else 'no_record'}},
              'entities': [entity], 'bulk_evidence': evidence}
    for name, source in supplemental.items():
        result['sources']['fns_' + name + '_bulk'] = {
            'status': 'ok' if company['inn'] in source.get('companies', {}) else 'no_record'}
    row, trace = project(result, request)
    # Replace generic projection paths with the actual origin of every bulk field.
    mapping = {'meta_company_name': 'name', 'entity_type': 'entity_type', 'main_okved': 'main_okved',
               'employees_count': 'employees', 'employees_year': 'employees',
               'tax_regime': 'tax_regime', 'tax_regime_period': 'tax_regime',
               'tax_debt_status': 'tax_debt', 'tax_debt_amount_rub': 'tax_debt', 'tax_debt_period': 'tax_debt'}
    for field, key in mapping.items():
        if key in owners and row.get(field) is not None:
            trace['field_sources'][field] = {'source': owners[key], **evidence[owners[key]]}
    for offset in range(3):
        year = row[f'financial_year_t{offset}']
        for field, key in (('income', 'income'), ('expenses', 'expenses'), ('taxes_paid', 'taxes_paid')):
            owner = financial_owners.get((year, key))
            if owner:
                trace['field_sources'][f'{field}_t{offset}_raw'] = {'source': owner, **evidence[owner]}
    for field, owner in (('meta_employees_data_date', 'msp'), ('meta_financials_data_date', 'income_expenses'),
                         ('meta_taxes_paid_data_date', 'paytax'), ('meta_tax_debt_data_date', 'debtam')):
        if owner in evidence and row[field] is not None:
            trace['field_sources'][field] = {'source': owner, **evidence[owner]}
    trace['bulk_evidence'] = evidence
    return result, row, trace


def build(pool, cache, parser_output, output, *, encoder_schema=None):
    started = time.monotonic()
    companies = [json.loads(line) for line in Path(pool).read_text(encoding='utf-8').splitlines() if line.strip()]
    keyed = {}
    for company in companies:
        request = normalize_request(company)
        if len(request['inn']) != 10 or request['identifier'] in keyed:
            raise ValueError('Expected unique legal companies with INN/OGRN')
        keyed[request['identifier']] = company
    requests = load_requests(Path(pool).with_name('identifiers.json'))
    if set(keyed) != {r['identifier'] for r in requests}:
        raise ValueError('Bulk pool differs from the original cohort')
    signature = hashlib.sha256('\n'.join(sorted(c['inn'] for c in companies)).encode()).hexdigest()
    supplemental = {}
    for name in RELEASES:
        path = cache / (name + '.json')
        if path.exists():
            source = read_json(path)
            if source['cohort_sha256'] != signature:
                raise ValueError('Supplemental release belongs to another cohort')
            supplemental[name] = source
    output.mkdir(parents=True, exist_ok=True)
    rows, traces, missing, statuses = [], [], [], Counter()
    tmp = output / 'companies.jsonl.part'
    with tmp.open('w', encoding='utf-8') as stream:
        for request in requests:
            company = keyed[request['identifier']]
            path = parser_output / 'records' / (request['identifier'] + '.json')
            record = read_json(path) if path.is_file() else None
            raw_path = result_path(parser_output, record) if record else None
            document = read_json(raw_path) if raw_path else None
            merged, row, trace = combine(company, supplemental, document)
            row['meta_source_file'] = str((output / 'companies.jsonl').resolve())
            row['meta_last_attempt_status'] = record['last_status'] if record else 'not_processed'
            trace['parser_source_file'] = str(raw_path) if raw_path else None
            trace['source_file'] = row['meta_source_file']
            rows.append(row)
            traces.append(trace)
            statuses[row['meta_parser_status']] += 1
            wanted = [f for f in ('registered_on', 'registry_status', 'capital_rub', 'founders_count',
                                 'largest_founder_share_pct', 'income_t0_raw', 'expenses_t0_raw',
                                 'income_t1_raw', 'expenses_t1_raw', 'income_t2_raw', 'expenses_t2_raw',
                                 'taxes_paid_t0_raw', 'tax_regime', 'tax_debt_amount_rub') if row[f] is None]
            if wanted:
                missing.append({**request, 'missing_fields': wanted,
                                'parser_attempt_status': row['meta_last_attempt_status']})
            stream.write(json.dumps(merged, ensure_ascii=False, allow_nan=False)+'\n')
    tmp.replace(output / 'companies.jsonl')
    report = export_tables(rows, traces, output, encoder_schema=encoder_schema)
    report.update(created_at=datetime.now(timezone.utc).isoformat(), source_mode='fns_bulk_plus_parser',
                  statuses=dict(statuses), elapsed_seconds=round(time.monotonic()-started, 3),
                  supplemental={name: {'matched_companies': s['matched_companies'], 'errors': s['errors'],
                                        'url': s['url']} for name, s in supplemental.items()},
                  companies_with_remaining_public_fields=len(missing),
                  loan_outcomes_collected=False, conversion_errors=[],
                  parser_output=str(parser_output.resolve()))
    atomic_json(output / 'report.json', report)
    atomic_json(output / 'enrichment_queue.json', missing)
    atomic_json(output / 'input.json', requests)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', type=Path, default=Path('output/scoring_pool_10000/companies.jsonl'))
    parser.add_argument('--cache', type=Path, default=Path('data/scoring_bulk_cache'))
    parser.add_argument('--parser-output', type=Path, default=Path('output/scoring_training'))
    parser.add_argument('--output', type=Path, default=Path('output/scoring_training_bulk'))
    parser.add_argument('--encoder-schema', type=Path)
    parser.add_argument('--fetch', action='store_true', help='Fetch the pinned official releases before export')
    args = parser.parse_args()
    with run_lock(args.output):
        if args.fetch:
            fetch_all(args.pool, args.cache)
        report = build(args.pool, args.cache, args.parser_output, args.output, encoder_schema=args.encoder_schema)
    print(json.dumps({key: report[key] for key in ('rows', 'statuses', 'elapsed_seconds', 'labelled_rows')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
