"""Download official FNS releases once and join only the requested INNs."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from .training import atomic_json, normalize_request

# Verified against the official dataset passports on 2026-10-24.
RELEASES = {
    'snr': ('20260925', '20230425'),
    'paytax': ('20260401', '20180110'),
    'debtam': ('20260925', '20181201'),
}


def source_url(name):
    release, structure = RELEASES[name]
    return f'https://file.nalog.ru/opendata/7707329152-{name}/data-{release}-structure-{structure}.zip'


def iso(value):
    return datetime.strptime(value, '%d.%m.%Y').date().isoformat()


def download(name, cache):
    url = source_url(name)
    path = cache / name / url.rsplit('/', 1)[-1]
    manifest = path.with_suffix('.manifest.json')
    if path.is_file() and manifest.is_file():
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if saved.get('url') == url and saved.get('bytes') == path.stat().st_size:
            return path, saved
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.part')
    for attempt in range(3):
        try:
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(url, timeout=30) as response, temp.open('wb') as out:
                expected = int(response.headers['Content-Length'])
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size != expected or not zipfile.is_zipfile(temp):
                raise ValueError('Incomplete or invalid FNS archive: ' + name)
            temp.replace(path)
            saved = {'url': url, 'bytes': size, 'sha256': digest.hexdigest(),
                     'downloaded_at': datetime.now(timezone.utc).isoformat()}
            atomic_json(manifest, saved)
            return path, saved
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def money_sum(elements, key):
    if not elements:
        raise ValueError('Missing monetary rows: ' + key)
    values = [Decimal(row.attrib[key]) for row in elements]
    if any(not value.is_finite() for value in values):
        raise ValueError('Non-finite monetary value')
    value = sum(values, Decimal(0))
    return int(value) if value == value.to_integral_value() else float(value)


def project_document(doc, name, member):
    result = {'inn': doc.find('СведНП').attrib['ИННЮЛ'], 'as_of': iso(doc.attrib['ДатаСост']),
              'published_on': iso(doc.attrib['ДатаДок']), 'source_url': source_url(name),
              'source_member': member, 'source_record': doc.attrib['ИдДок']}
    if name == 'snr':
        flags = doc.find('СведСНР').attrib
        mapping = {'ПризнУСН': 'УСН', 'ПризнЕСХН': 'ЕСХН', 'ПризнАУСН': 'АУСН', 'ПризнСРП': 'СРП'}
        if any(flags.get(key) not in {'0', '1'} for key in mapping):
            raise ValueError('Unknown tax regime flag')
        result['regimes'] = sorted(value for key, value in mapping.items() if flags[key] == '1')
    elif name == 'paytax':
        result['taxes_paid_rub'] = money_sum(doc.findall('СвУплСумНал'), 'СумУплНал')
        # This release's passport explicitly reports calendar year 2025.
        # DateSost is retained separately and must not be guessed to be a year.
        result['year'] = 2025
    elif name == 'debtam':
        result['amount_rub'] = money_sum(doc.findall('СведНедоим'), 'ОбщСумНедоим')
        if result['amount_rub'] < 0:
            raise ValueError('Negative total tax debt')
        result['status'] = 'has_debt' if result['amount_rub'] > 0 else 'no_debt'
    return result


def parse_archive(name, archive, wanted):
    found, errors, scanned = {}, [], 0
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if not member.filename.lower().endswith('.xml'):
                continue
            with z.open(member) as source:
                for _, doc in ET.iterparse(source, events=('end',)):
                    if doc.tag != 'Документ':
                        continue
                    scanned += 1
                    taxpayer = doc.find('СведНП')
                    inn = taxpayer.get('ИННЮЛ') if taxpayer is not None else None
                    if inn in wanted:
                        try:
                            item = project_document(doc, name, member.filename)
                            old = found.get(inn)
                            if old and old != item:
                                # A conflict is unknown, never an arbitrary last-row win.
                                raise ValueError('Conflicting duplicate INN in release')
                            found[inn] = item
                        except (ValueError, KeyError, AttributeError, ArithmeticError) as exc:
                            found.pop(inn, None)
                            errors.append({'inn': inn, 'member': member.filename, 'error': str(exc)})
                    doc.clear()
    # Do not let a later duplicate restore an INN already marked invalid.
    for error in errors:
        found.pop(error['inn'], None)
    return found, {'scanned_records': scanned, 'matched_companies': len(found), 'errors': errors}


def fetch_all(pool, cache):
    companies = [json.loads(line) for line in Path(pool).read_text(encoding='utf-8').splitlines() if line.strip()]
    wanted = {normalize_request(row)['inn'] for row in companies}
    signature = hashlib.sha256('\n'.join(sorted(wanted)).encode()).hexdigest()
    for name in RELEASES:
        output = cache / (name + '.json')
        if output.exists():
            cached = json.loads(output.read_text(encoding='utf-8'))
            if cached.get('cohort_sha256') == signature and cached.get('url') == source_url(name):
                print(f'{name}: cached {len(cached["companies"])} companies', flush=True)
                continue
        print(f'{name}: downloading {source_url(name)}', flush=True)
        path, manifest = download(name, cache)
        print(f'{name}: joining archive ({manifest["bytes"]} bytes)', flush=True)
        found, report = parse_archive(name, path, wanted)
        atomic_json(output, {**manifest, **report, 'cohort_sha256': signature, 'companies': found})
        print(f'{name}: matched {len(found)} of {len(wanted)}; conversion errors {len(report["errors"])}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', type=Path, default=Path('output/scoring_pool_10000/companies.jsonl'))
    parser.add_argument('--cache', type=Path, default=Path('data/scoring_bulk_cache'))
    args = parser.parse_args()
    fetch_all(args.pool, args.cache)


if __name__ == '__main__':
    main()
