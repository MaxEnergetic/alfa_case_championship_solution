"""Historical bankruptcy cohort from free notices and RFSD financial statements.

The event label is separate from loan default. A reporting period before an
event does NOT establish point-in-time availability: RFSD lacks filing dates
and stores the latest revisions. Such rows remain retrospective research data.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from .identifiers import validate_identifier
from .training import (atomic_json, export_tables, project, TEMPLATE_COLUMNS, EXTRA_COLUMNS)


MONTHS = {v: i for i, v in enumerate(
    ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
     'сентября', 'октября', 'ноября', 'декабря'), 1)}
CASE = re.compile(r'[АA]\s*\d{1,3}\s*[-–]\s*\d+\s*/\s*(?:20)?\d{2}(?!\d)')
ENTITY = re.compile(r'\b(?:ООО|ОАО|ЗАО|ПАО|АО|ФГУП|МУП|ГУП|СПК|СХПК)\b|'
                    r'\b(?:Обществ[оа]\s+с\s+ограниченной\s+ответственностью|'
                    r'(?:Открытое|Закрытое|Публичное)?\s*акционерное\s+общество)\b', re.I)
EVENT = re.compile(r'признан[ао]?\s+(?:несостоятельн\w*\s*\(банкротом\)|банкротом)|'
                   r'(?:открыт[оа]?|введен[оа]?)\s+(?:процедура\s+)?конкурсно(?:е|го)\s+производств', re.I)
AMOUNTS = {
    'revenue': 'line_2110', 'net_profit': 'line_2400', 'profit_before_tax': 'line_2300',
    'assets': 'line_1600', 'noncurrent_assets': 'line_1100', 'current_assets': 'line_1200',
    'inventory': 'line_1210', 'receivables': 'line_1230', 'cash': 'line_1250',
    'equity': 'line_1300', 'long_term_liabilities': 'line_1400',
    'short_term_liabilities': 'line_1500', 'short_term_borrowings': 'line_1510',
    'payables': 'line_1520',
}
HISTORICAL_META = [
    'meta_cohort', 'meta_bankruptcy_case', 'meta_bankruptcy_event_date',
    'meta_bankruptcy_notice_url', 'meta_bankruptcy_notice_id',
    'meta_bankruptcy_evidence_status', 'meta_historical_availability_status',
    'meta_financial_publication_date', 'meta_point_in_time_verified',
    'meta_bankruptcy_training_eligible', 'meta_rfsd_release_version',
    'meta_rfsd_imputation_policy', 'meta_report_period_before_event',
    'meta_bankruptcy_target_definition', 'meta_bankruptcy_horizon_end',
    'target_bankruptcy_12m',
]
RAS_COLUMNS = [f'ras_{key}_t{offset}_rub' for offset in range(3) for key in AMOUNTS]
ADDITIONAL_COLUMNS = HISTORICAL_META + RAS_COLUMNS


def now():
    return datetime.now(timezone.utc).isoformat()


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    with temporary.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def read_jsonl(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def normalize_notice(text):
    text = text.replace('\u00ad', '').replace('\u00a0', ' ')
    text = re.sub(r'(?<=[А-Яа-я])-\s*\n(?=[а-я])', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def dates_in(text):
    found = []
    for match in re.finditer(r'(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*((?:20)?\d{2})(?!\d)', text):
        day, month, year = map(int, match.groups())
        try:
            found.append(date(year + 2000 if year < 100 else year, month, day))
        except ValueError:
            pass
    for match in re.finditer(r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(20\d{2})', text, re.I):
        try:
            found.append(date(int(match[3]), MONTHS[match[2].lower()], int(match[1])))
        except ValueError:
            pass
    return sorted(set(found))


def parse_notice(text, *, notice_id, page, source):
    body = normalize_notice(text)
    if not body.lower().startswith('решением'):
        return None
    event = EVENT.search(body)
    if event is None:
        return None
    # Only the sentence identifying the bankrupt debtor, never the administrator,
    # SRO, auction winner, creditor or a later procedural decision.
    head = body[:event.start()]
    if re.search(r'отменен|отменён|отказано', head, re.I):
        return None
    inn_matches = list(re.finditer(r'ИНН\s*[:/]?\s*(\d{10})(?!\d)', head))
    ogrn_matches = list(re.finditer(r'ОГРН\s*[:/]?\s*(\d{13})(?!\d)', head))
    if len({m[1] for m in inn_matches}) != 1 or len({m[1] for m in ogrn_matches}) != 1:
        return None
    inn, ogrn = inn_matches[0][1], ogrn_matches[0][1]
    try:
        validate_identifier(inn)
        validate_identifier(ogrn)
    except ValueError:
        return None
    first_identifier = min(inn_matches[0].start(), ogrn_matches[0].start())
    entity = ENTITY.search(head[:first_identifier])
    case = CASE.search(head)
    if entity is None or case is None:
        return None
    if re.search(r'ИНН\s*[:/]?\s*\d{12}(?!\d)', head[:entity.start()]):
        return None  # Do not attach a citizen's bankruptcy to the SRO.
    court_header = head[:entity.start()]
    event_dates = dates_in(court_header)
    if not event_dates:
        return None
    if len(event_dates) > 2 or (event_dates[-1] - event_dates[0]).days > 45:
        return None  # Ambiguous history needs human review.
    event_date = event_dates[0]
    published = date.fromisoformat(source['published_on'])
    if not date(2011, 1, 1) <= event_date <= published:
        return None
    case_number = re.sub(r'\s+', '', case[0]).replace('A', 'А').replace('–', '-')
    start, year = case_number.rsplit('/', 1)
    year = int(year) + 2000 if len(year) == 2 else int(year)
    if year > event_date.year:
        return None
    case_number = start + '/' + str(year)
    name = head[entity.start():first_identifier].strip(' (,;')
    name = name.split('(', 1)[0].strip(' ,;')
    if len(name) > 250:
        return None
    excerpt = body[:event.end()]
    return {
        'inn': inn, 'ogrn': ogrn, 'name': name, 'event_date': event_date.isoformat(),
        'event_date_basis': 'earliest_explicit_decision_or_operative_part_date_in_notice',
        'decision_dates_in_notice': [d.isoformat() for d in event_dates],
        'case_number': case_number,
        'event_type': 'court_bankruptcy_or_liquidation_proceedings',
        'evidence_status': 'published_court_event_subsequent_appeals_not_checked',
        'sources': [{'url': source['url'], 'published_on': source['published_on'],
                     'notice_id': notice_id, 'page': page, 'pdf_sha256': source['sha256'],
                     'excerpt': excerpt}],
    }


def collect(manifest, output):
    import pymupdf
    sources = json.loads(Path(manifest).read_text(encoding='utf-8'))
    grouped, conflicting, counts = {}, {}, {}
    for source in sources:
        path = Path(source['local_file'])
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != source['sha256']:
                raise ValueError('Source PDF hash mismatch: ' + str(path))
        count = 0
        with pymupdf.open(path) as pdf:
            for page_number, page in enumerate(pdf, 1):
                pieces = re.split(r'(?m)^\s*(\d{11})\s*$', page.get_text())
                for index in range(1, len(pieces) - 1, 2):
                    company = parse_notice(pieces[index + 1], notice_id=pieces[index],
                                           page=page_number, source=source)
                    if company is None:
                        continue
                    count += 1
                    key = company['inn']
                    old = grouped.get(key)
                    if old is None:
                        grouped[key] = company
                    elif (old['ogrn'], old['case_number'], old['event_date']) == (
                            company['ogrn'], company['case_number'], company['event_date']):
                        old['sources'].extend(company['sources'])
                    else:
                        conflicting.setdefault(key, [old]).append(company)
        counts[source['url']] = count
        print(path.name, 'accepted notices', count, flush=True)
    for key in conflicting:
        grouped.pop(key, None)
    companies = sorted(grouped.values(), key=lambda x: x['inn'])
    write_jsonl(output / 'candidates.jsonl', companies)
    atomic_json(output / 'conflicting_events.json', conflicting)
    atomic_json(output / 'collection_report.json', {
        'created_at': now(), 'candidate_companies': len(companies),
        'conflicting_inns_excluded': len(conflicting), 'accepted_notices_by_source': counts,
        'event_years': dict(Counter(c['event_date'][:4] for c in companies)),
        'scope': 'Free publisher PDFs; legal entities with valid INN and OGRN; explicit court event only',
    })
    return companies


def valid_report(row, company, year, cutoff):
    if row.get('inn') != company['inn'] or row.get('ogrn') != company['ogrn']:
        return False
    if date(year, 12, 31) >= date.fromisoformat(cutoff):
        return False
    if row.get('filed') != 1 or row.get('imputed') != 0 or row.get('totals_adjustment') != 0:
        return False
    if row.get('financial') != 0 or row.get('outlier') != 0:
        return False
    return sum(isinstance(row.get(AMOUNTS[k]), (int, float)) and math.isfinite(row[AMOUNTS[k]])
               for k in ('revenue', 'net_profit', 'assets', 'equity')) >= 2


def join_financials(candidates, cache, output):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    histories = {}
    metadata = {}
    columns = ['inn', 'ogrn', 'creation_date', 'okved', 'region_taxcode', 'filed', 'imputed',
               'totals_adjustment', 'financial', 'outlier', 'simplified', 'articulated'] + list(AMOUNTS.values())
    for parquet in sorted(cache.glob('*.parquet')):
        year = int(parquet.stem)
        if not 2012 <= year <= 2018:
            raise ValueError('This importer supports only verified Rosstat RFSD partitions (2012–2018)')
        wanted = {c['inn']: c for c in candidates
                  if 2 <= int(c['event_date'][:4]) - year <= 4}
        if not wanted:
            continue
        meta = json.loads(parquet.with_name(str(year) + '.manifest.json').read_text(encoding='utf-8'))
        with parquet.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != meta['sha256']:
                raise ValueError('RFSD hash mismatch')
        keys = pa.array(sorted(wanted), type=pa.string())
        file = pq.ParquetFile(parquet)
        matched, accepted = 0, 0
        duplicate_keys = set()
        for batch in file.iter_batches(batch_size=65536, columns=columns):
            filtered = batch.filter(pc.is_in(batch.column('inn'), value_set=keys))
            for record in filtered.to_pylist():
                matched += 1
                company = wanted[record['inn']]
                cutoff = f"{int(company['event_date'][:4]) - 1}-12-31"
                if not valid_report(record, company, year, cutoff):
                    continue
                key = (record['inn'], year)
                if year in histories.get(record['inn'], {}):
                    duplicate_keys.add(key)
                    continue
                record = {k: v.isoformat() if isinstance(v, date) else
                          None if isinstance(v, float) and not math.isfinite(v) else v
                          for k, v in record.items()}
                histories.setdefault(record['inn'], {})[year] = record
                accepted += 1
        for inn, conflict_year in duplicate_keys:
            histories[inn].pop(conflict_year, None)
        metadata[year] = meta
        print(year, 'matched', matched, 'accepted original reports', accepted - len(duplicate_keys), flush=True)
    atomic_json(output / 'histories.json', {'metadata': metadata, 'companies': histories})
    return histories, metadata


def historical_row(company, history, metadata):
    event = date.fromisoformat(company['event_date'])
    cutoff = date(event.year - 1, 12, 31)
    latest = max(history)
    report = history[latest]
    if any(not valid_report(values, company, year, cutoff.isoformat()) for year, values in history.items()):
        raise ValueError('Historical report failed identity, period or quality validation')
    entity = {'inn': company['inn'], 'ogrn': company['ogrn'], 'name': company['name'],
              'entity_type': 'legal', 'registered_on': report.get('creation_date'),
              'main_okved': report.get('okved'), 'financials': [{'year': latest}]}
    if entity['registered_on'] and date.fromisoformat(str(entity['registered_on'])[:10]) > cutoff:
        raise ValueError('Incorporation after historical cutoff')
    entity['registered_on'] = str(entity['registered_on'])[:10] if entity['registered_on'] else None
    document = {'schema_version': 'compact-1.0', 'query': company['ogrn'],
                'checked_at': cutoff.isoformat(), 'status': 'historical_rfsd', 'sources': {},
                'entities': [entity]}
    row, trace = project(document, company)
    row.update(dict.fromkeys(ADDITIONAL_COLUMNS))
    row.update({
        'meta_cohort': 'bankruptcy_historical', 'meta_bankruptcy_case': company['case_number'],
        'meta_bankruptcy_event_date': company['event_date'],
        'meta_bankruptcy_notice_url': company['sources'][0]['url'],
        'meta_bankruptcy_notice_id': company['sources'][0]['notice_id'],
        'meta_bankruptcy_evidence_status': company['evidence_status'],
        'meta_historical_availability_status': 'report_period_before_cutoff_publication_and_revision_dates_unknown',
        'meta_point_in_time_verified': 0, 'meta_bankruptcy_training_eligible': 0,
        'meta_report_period_before_event': 1, 'meta_rfsd_release_version': '3.1.0',
        'meta_bankruptcy_target_definition': 'published_court_bankruptcy_event_next_calendar_year_v1',
        'meta_bankruptcy_horizon_end': date(event.year, 12, 31).isoformat(),
        'meta_rfsd_imputation_policy': 'filed=1; imputed=0; totals_adjustment=0; outlier=0; financial=0',
        # This is a bankruptcy event within a calendar-year horizon. It is never
        # substituted for the template's observed bank-loan default target.
        'target_bankruptcy_12m': 1,
    })
    trace['bankruptcy_evidence'] = company
    trace['historical_cutoff'] = cutoff.isoformat()
    trace['point_in_time_verified'] = False
    trace['publication_date'] = None
    trace['field_sources']['registered_on'] = {'source': 'RFSD', 'year': latest, 'field': 'creation_date'}
    trace['field_sources']['main_okved'] = {'source': 'RFSD_historical_harmonized_code', 'year': latest, 'field': 'okved'}
    for offset in range(3):
        year = latest - offset
        values = history.get(year)
        if values is None:
            continue
        for key, line in AMOUNTS.items():
            value = values.get(line)
            if value is None or not math.isfinite(value):
                continue
            # RFSD Rosstat normalization explicitly uses thousands of RUB.
            amount = float(value) * 1000
            field = f'ras_{key}_t{offset}_rub'
            row[field] = int(amount) if amount.is_integer() else amount
            trace['field_sources'][field] = {
                'source': 'RFSD_Rosstat', 'url': metadata[year]['url'], 'sha256': metadata[year]['sha256'],
                'report_year': year, 'period_end': f'{year}-12-31', 'available_on': None,
                'source_field': line, 'source_units': 'thousand_rub', 'multiplier': 1000,
                'filed': 1, 'imputed': 0, 'totals_adjustment': 0,
            }
    return row, trace


def decode_base_rows(path):
    import csv
    rows = []
    from .training import NUMERIC
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for saved in csv.DictReader(stream):
            row = {}
            for field in TEMPLATE_COLUMNS + EXTRA_COLUMNS:
                if saved[field + '_missing'] == '1':
                    value = None
                elif field in NUMERIC:
                    value = float(saved[field])
                    value = int(value) if value.is_integer() else value
                elif field == 'tax_regime':
                    value = saved[field].split('|')
                else:
                    value = saved[field]
                row[field] = value
            row.update(dict.fromkeys(ADDITIONAL_COLUMNS))
            row['meta_cohort'] = 'original_10000_unlabelled'
            rows.append(row)
    return rows


def build(candidates, histories, metadata, output, base, count):
    if count < 1:
        raise ValueError('count must be positive')
    base_rows = decode_base_rows(base / 'dataset_raw.csv')
    base_inns = {r['meta_inn'] for r in base_rows}
    base_ogrns = {r['meta_ogrn'] for r in base_rows}
    if len(base_rows) != len(base_inns) or len(base_rows) != len(base_ogrns):
        raise ValueError('Base dataset has duplicate INN or OGRN')
    eligible = [c for c in candidates if histories.get(c['inn']) and c['inn'] not in base_inns]
    eligible.sort(key=lambda c: hashlib.sha256(('20261025:' + c['inn']).encode()).hexdigest())
    selected = eligible[:count]
    if len(selected) != count:
        raise ValueError(f'Only {len(selected)} candidates with original historical financials; need {count}')
    rows, traces = [], []
    cohort = output / 'bankruptcies'
    for company in selected:
        history = {int(y): v for y, v in histories[company['inn']].items()}
        row, trace = historical_row(company, history, metadata)
        source_file = cohort / 'records' / (company['ogrn'] + '.json')
        row['meta_source_file'] = str(source_file.resolve())
        trace['source_file'] = str(source_file.resolve())
        atomic_json(source_file, {'schema_version': 'historical-bankruptcy-research-1.0',
                                 'fields': row, 'bankruptcy_evidence': company,
                                 'financial_reports': history, 'provenance': trace})
        rows.append(row)
        traces.append(trace)
    if len({r['meta_ogrn'] for r in rows} - base_ogrns) != len(rows):
        raise ValueError('Duplicate OGRN in addition or overlap with base dataset')
    conversion_errors = [{'inn': row['meta_inn'], 'fields': trace['invalid_or_unavailable_values']}
                         for row, trace in zip(rows, traces) if trace['invalid_or_unavailable_values']]
    if conversion_errors:
        raise ValueError('Invalid projected values: ' + str(conversion_errors))
    cohort_report = export_tables(rows, traces, cohort, additional_columns=ADDITIONAL_COLUMNS)
    combined = base_rows + rows
    base_traces = read_jsonl(base / 'field_sources.jsonl')
    if len(base_traces) != len(base_rows):
        raise ValueError('Base provenance row count mismatch')
    report = export_tables(combined, base_traces + traces, output, additional_columns=ADDITIONAL_COLUMNS)
    write_jsonl(output / 'companies.jsonl', combined)
    write_jsonl(cohort / 'evidence.jsonl', selected)
    atomic_json(output / 'identifiers.json', [{'inn': r['meta_inn'], 'ogrn': r['meta_ogrn']} for r in combined])
    atomic_json(cohort / 'identifiers.json', [{'inn': c['inn'], 'ogrn': c['ogrn']} for c in selected])
    atomic_json(output / 'bankruptcies' / 'report.json', cohort_report)
    report.update({
        'created_at': now(), 'original_rows': len(base_rows), 'added_bankrupt_companies': len(rows),
        'unique_inns': len({r['meta_inn'] for r in combined}),
        'unique_ogrns': len({r['meta_ogrn'] for r in combined}),
        'bankruptcy_event_years': dict(Counter(c['event_date'][:4] for c in selected)),
        'financial_years_t0': dict(Counter(r['financial_year_t0'] for r in rows)),
        'known_bankruptcy_event_labels': len(rows), 'loan_default_labels_added': 0,
        'point_in_time_verified_bankruptcy_rows': 0,
        'conversion_errors': conversion_errors,
        'known_financial_values_added': sum(r[c] is not None for r in rows for c in RAS_COLUMNS),
        'rows_with_revenue': sum(r['ras_revenue_t0_rub'] is not None for r in rows),
        'rows_with_net_profit': sum(r['ras_net_profit_t0_rub'] is not None for r in rows),
        'rows_with_assets': sum(r['ras_assets_t0_rub'] is not None for r in rows),
        'rows_with_equity': sum(r['ras_equity_t0_rub'] is not None for r in rows),
        'purpose': 'Retrospective research dataset; not a validated point-in-time training set',
        'limitations': [
            'RFSD lacks original publication and revision dates; all new rows have point-in-time verification=0.',
            'Rosstat source conflates zero and missing; RFSD replaces these zeros with NA, retained here as unknown.',
            'Bankruptcy events are historical notices; later cancellation or appeal has not been checked for every case.',
            'Original 10000 firms remain unlabelled; absence of an event is never assigned target=0.',
            'Current original cohort and historical bankruptcy cohort have different dates and source coverage.',
            'Financial years and missingness can identify the cohort; obtain comparable dated controls before training.',
            'Bankruptcy target is separate from bank-loan default; annual revenue is separate from FNS aggregate income.',
        ],
    })
    atomic_json(output / 'report.json', report)
    schema = json.loads((output / 'encoder.json').read_text(encoding='utf-8'))
    leaked_columns = [c for c in schema['feature_columns'] if c.startswith(('meta_', 'target_', 'outcome_'))]
    if leaked_columns:
        raise ValueError('Target/metadata in feature matrix: ' + str(leaked_columns))
    atomic_json(output / 'validation.json', {
        'rows': len(combined), 'unique_inns': report['unique_inns'], 'unique_ogrns': report['unique_ogrns'],
        'expected_addition': count, 'actual_addition': len(rows),
        'all_new_rows_have_original_financials': all(any(r[c] is not None for c in RAS_COLUMNS) for r in rows),
        'all_new_report_periods_before_cutoff': all(
            f"{r['financial_year_t0']}-12-31" < r['meta_features_as_of'] < r['meta_bankruptcy_event_date'] for r in rows),
        'default_labels_added': sum(r['target_default_12m'] is not None for r in rows),
        'strict_point_in_time_training_ready': False,
        'target_or_event_feature_columns': leaked_columns,
    })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('collect', 'join', 'build', 'all'))
    parser.add_argument('--manifest', type=Path, default=Path('data/bankruptcy_cache/gazettes/manifest.json'))
    parser.add_argument('--cache', type=Path, default=Path('data/bankruptcy_cache/rfsd'))
    parser.add_argument('--work', type=Path, default=Path('output/bankruptcy_pool'))
    parser.add_argument('--base', type=Path, default=Path('output/scoring_training_bulk'))
    parser.add_argument('--output', type=Path, default=Path('output/scoring_training_with_bankruptcies'))
    parser.add_argument('--count', type=int, default=1000)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.stage in ('collect', 'all'):
        collect(args.manifest, args.work)
    if args.stage == 'collect':
        return
    candidates = read_jsonl(args.work / 'candidates.jsonl')
    if args.stage in ('join', 'all'):
        join_financials(candidates, args.cache, args.work)
    if args.stage == 'join':
        return
    joined = json.loads((args.work / 'histories.json').read_text(encoding='utf-8'))
    report = build(candidates, joined['companies'], {int(k): v for k, v in joined['metadata'].items()},
                   args.output, args.base, args.count)
    print(json.dumps({k: report[k] for k in ('rows', 'added_bankrupt_companies', 'labelled_rows',
                                           'point_in_time_verified_bankruptcy_rows')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
