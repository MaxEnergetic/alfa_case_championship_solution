"""Download publicly free gazette PDFs and pinned RFSD Rosstat partitions.

No account, CAPTCHA bypass or paid download. Network errors stop the command;
rerunning preserves downloaded, hash-verified sources.
"""
import argparse
from datetime import datetime, timezone, date
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin
from urllib.request import urlopen

from .training import atomic_json

PUBLISHER = 'https://xn--80aj3aega.xn--p1ai'
HF = 'https://huggingface.co'
RFSD_REVISION = 'bde9b8b129a8be582b66de4e30ed79c9187a8515'
DATES = ['2015-01-24', '2015-03-28', '2015-05-30', '2015-07-25', '2015-09-26',
         '2015-11-28', '2015-02-21', '2015-11-14', '2015-12-26', '2019-11-30', '2020-06-11',
         '2015-01-31', '2015-02-07', '2015-03-07', '2015-04-25', '2015-06-27']


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def get(url):
    with urlopen(url, timeout=45) as response:
        return response.read()


def free_pdf_link(html, page_url):
    matches = re.findall(r'<a\s+class="[^"]*download_pdf[^"]*"\s+href="([^"]+)"', html)
    if not matches or 'Бесплатно' not in html:
        return None
    url = urljoin(page_url, matches[0])
    if not url.startswith(PUBLISHER + '/files/'):
        raise ValueError('Unexpected publisher PDF location')
    return url


def fetch_gazettes(output, dates):
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    for day in dates:
        date.fromisoformat(day)
        cached = [m for m in manifest if m['published_on'] == day]
        if cached and all(Path(m['local_file']).exists() and digest(m['local_file']) == m['sha256'] for m in cached):
            continue
        archive_url = PUBLISHER + '/ru/magazines/kommersant/archive?date_start=' + day + '&date_end=' + day
        html = get(archive_url).decode('utf-8')
        links = list(dict.fromkeys(re.findall(r'href="(/ru/magazines/kommersant/\d[^"/]+)"', html)))
        for link in links:
            page_url = urljoin(PUBLISHER, link)
            html_bytes = get(page_url)
            url = free_pdf_link(html_bytes.decode('utf-8'), page_url)
            if url is None:
                print('SKIP: no public free PDF link:', page_url, flush=True)
                continue
            target = output / url.rsplit('/', 1)[-1]
            data = get(url)
            if not data.startswith(b'%PDF'):
                raise ValueError('Publisher response is not PDF')
            temporary = target.with_suffix('.pdf.part')
            temporary.write_bytes(data)
            temporary.replace(target)
            # Keep the public listing as evidence of the free download link.
            target.with_suffix('.listing.html').write_bytes(html_bytes)
            manifest = [m for m in manifest if m['catalog_url'] != page_url]
            manifest.append({'url': url, 'catalog_url': page_url, 'published_on': day,
                             'local_file': str(target.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                             'access': 'publisher_free_download_link',
                             'downloaded_at': datetime.now(timezone.utc).isoformat()})
            atomic_json(path, manifest)
            print(day, target.name, len(data), flush=True)
            time.sleep(1)


def fetch_rfsd(output, years):
    output.mkdir(parents=True, exist_ok=True)
    for year in years:
        if not 2012 <= year <= 2018:
            raise ValueError('Only verified Rosstat partitions 2012–2018 are supported')
        path = output / f'{year}.parquet'
        meta_path = output / f'{year}.manifest.json'
        if path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if meta['revision'] == RFSD_REVISION and digest(path) == meta['sha256']:
                print(year, 'cached', flush=True)
                continue
        listing = json.loads(get(f'{HF}/api/datasets/irlspbru/RFSD/tree/{RFSD_REVISION}/RFSD/year={year}'))
        entry, = [x for x in listing if x['type'] == 'file' and x['path'].endswith('.parquet')]
        url = f'{HF}/datasets/irlspbru/RFSD/resolve/{RFSD_REVISION}/' + entry['path']
        temporary = path.with_suffix('.parquet.part')
        checksum, size = hashlib.sha256(), 0
        with urlopen(url, timeout=45) as response, temporary.open('wb') as stream:
            while chunk := response.read(4 * 1024 * 1024):
                stream.write(chunk)
                checksum.update(chunk)
                size += len(chunk)
        if size != entry['size'] or checksum.hexdigest() != entry['lfs']['oid']:
            raise ValueError('RFSD size or SHA256 mismatch')
        temporary.replace(path)
        atomic_json(meta_path, {'url': url, 'revision': RFSD_REVISION, 'bytes': size,
                               'sha256': checksum.hexdigest(), 'report_year': year,
                               'downloaded_at': datetime.now(timezone.utc).isoformat(),
                               'license': 'CC-BY-4.0', 'dataset_version': '3.1.0'})
        print(year, 'downloaded', size, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('gazettes', 'rfsd', 'all'), default='all')
    parser.add_argument('--cache', type=Path, default=Path('data/bankruptcy_cache'))
    parser.add_argument('--dates', nargs='+', default=DATES)
    parser.add_argument('--years', nargs='+', type=int, default=[2013, 2017, 2018])
    args = parser.parse_args()
    if args.source in ('gazettes', 'all'):
        fetch_gazettes(args.cache / 'gazettes', args.dates)
    if args.source in ('rfsd', 'all'):
        fetch_rfsd(args.cache / 'rfsd', args.years)


if __name__ == '__main__':
    main()
