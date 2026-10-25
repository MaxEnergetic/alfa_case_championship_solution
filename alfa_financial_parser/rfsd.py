"""Read one INN from immutable RFSD Parquet files using checked HTTP ranges.

Only the public RFSD data repository is accessed. No FNS private endpoints,
credentials, browser automation, CAPTCHA handling or inferred API contracts.
"""
import gzip,hashlib,json,os,re,struct,tempfile,time
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from urllib.parse import quote
from .parquet import Compact,column

DATASET='irlspbru/RFSD'
ROOT_URL='https://huggingface.co'
FIELDS=['inn','ogrn','creation_date','dissolution_date','okved','okved_section',
 'filed','financial','imputed','outlier','articulated','totals_adjustment']+[
 'line_'+str(code) for code in [1200,1250,1300,1400,1500,1600,1700,2110,2200,2330,2400]]

class SourceError(RuntimeError):pass

def atomic_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

class RfsdClient:
    def __init__(self,cache_dir='.cache/alfa_financial',timeout=45,offline=False):
        self.cache=Path(cache_dir);self.timeout=float(timeout);self.offline=offline
        if self.timeout<=0:raise ValueError('timeout must be positive')

    def _request(self,url,headers=None,limit=5_000_000,expected_range=None):
        if self.offline:raise SourceError('Offline cache miss: run once online with the same revision and year')
        for attempt in range(2):
            try:
                req=Request(url,headers={'User-Agent':'alfa-financial-parser/0.1','Accept-Encoding':'identity',**(headers or {})})
                with urlopen(req,timeout=self.timeout) as response:
                    if expected_range is not None:
                        a,b=expected_range;cr=response.headers.get('Content-Range','')
                        if response.status!=206 or not re.fullmatch(rf'bytes {a}-{b}/\d+',cr):
                            raise SourceError('Source did not honor the requested byte range')
                    data=response.read(limit+1)
                    if len(data)>limit:raise SourceError('Source response exceeds the expected size')
                    if expected_range is not None and len(data)!=limit:raise SourceError('Truncated range response')
                    return data
            except HTTPError as e:
                if e.code in (502,503,504) and attempt==0:time.sleep(1);continue
                raise SourceError(f'RFSD HTTP {e.code}; no access restriction is bypassed') from e
            except (URLError,TimeoutError,OSError) as e:
                if attempt==0:time.sleep(1);continue
                raise SourceError(f'RFSD network request failed: {type(e).__name__}') from e
        raise SourceError('RFSD request failed')

    def _json(self,url,cache_path=None):
        from_cache=cache_path is not None and Path(cache_path).exists()
        if from_cache:
            data=Path(cache_path).read_bytes()
        else:
            data=self._request(url,{'Accept':'application/json'})
        try:parsed=json.loads(data)
        except (ValueError,UnicodeError) as e:raise SourceError('Source returned invalid JSON') from e
        if cache_path is not None and not from_cache:atomic_write(cache_path,data)
        return parsed

    def resolve_revision(self,revision='main'):
        if re.fullmatch('[0-9a-f]{40}',revision):return revision
        if revision!='main':raise ValueError('revision must be main or an exact 40-character commit SHA')
        if self.offline:raise SourceError('Offline mode requires an explicit --revision and --year')
        data=self._json(f'{ROOT_URL}/api/datasets/{DATASET}')
        sha=data.get('sha','')
        if not re.fullmatch('[0-9a-f]{40}',sha):raise SourceError('Missing immutable dataset revision')
        return sha

    def available_years(self,revision):
        path=self.cache/revision/'years.json'
        rows=self._json(f'{ROOT_URL}/api/datasets/{DATASET}/tree/{revision}/RFSD?limit=1000',path)
        if not isinstance(rows,list):raise SourceError('Unexpected RFSD directory response')
        years=[]
        for item in rows:
            m=re.fullmatch(r'RFSD/year=(\d{4})',item.get('path',''))
            if item.get('type')=='directory' and m:years.append(int(m.group(1)))
        if not years:raise SourceError('No annual RFSD partitions found')
        return sorted(years)

    def _descriptor(self,revision,year):
        folder=self.cache/revision/str(year)
        rows=self._json(f'{ROOT_URL}/api/datasets/{DATASET}/tree/{revision}/RFSD/year%3D{year}?expand=true',folder/'descriptor.json')
        files=[x for x in rows if x.get('type')=='file' and x.get('path','').endswith('.parquet')]
        expected=f'RFSD/year={year}/part-0.parquet'
        if len(files)!=1 or files[0].get('path')!=expected:
            raise SourceError('RFSD partition layout changed: this adapter expects exactly part-0.parquet')
        item=files[0]
        if not isinstance(item.get('size'),int) or item['size']<12:raise SourceError('Invalid source file size')
        if not item.get('lastCommit',{}).get('date'):raise SourceError('Source availability date is missing')
        return item

    def _range(self,url,a,b,cache_path):
        size=b-a+1
        if a<0 or size<=0 or size>128_000_000:raise SourceError('Invalid or excessive byte range')
        p=Path(cache_path)
        if p.exists():
            data=p.read_bytes()
            if len(data)==size:return data
            raise SourceError('Cached range is truncated; remove this revision cache and retry')
        data=self._request(url,{'Range':f'bytes={a}-{b}'},limit=size,expected_range=(a,b))
        atomic_write(p,data);return data

    def _index(self,revision,year,descriptor):
        folder=self.cache/revision/str(year);cached=folder/'index.json.gz'
        if cached.exists():
            try:return json.loads(gzip.decompress(cached.read_bytes()))
            except (ValueError,OSError) as e:raise SourceError('Invalid cached index; remove this revision cache') from e
        url=f'{ROOT_URL}/datasets/{DATASET}/resolve/{revision}/RFSD/year%3D{year}/part-0.parquet'
        size=descriptor['size'];tail=self._range(url,size-8,size-1,folder/'footer.tail')
        if tail[-4:]!=b'PAR1':raise SourceError('Unexpected Parquet file signature')
        length=struct.unpack('<I',tail[:4])[0]
        raw=self._range(url,size-8-length,size-9,folder/'footer.metadata')
        parser=Compact(raw);meta=parser.obj()
        if parser.pos!=len(raw):raise SourceError('Unread footer metadata bytes')
        schema={s[4].decode():s for s in meta[2][1:]}
        if not set(FIELDS)<=set(schema):raise SourceError('RFSD required columns are missing')
        optional={k:schema[k][3]==1 for k in FIELDS};groups=[]
        for number,g in enumerate(meta[4]):
            allcols={c[3][3][0].decode():c[3] for c in g[1]}
            stats=allcols['inn'].get(12,{})
            lo=stats.get(6,stats.get(2));hi=stats.get(5,stats.get(1))
            if not isinstance(lo,bytes) or not isinstance(hi,bytes):raise SourceError('INN range statistics are missing')
            columns={}
            for k in FIELDS:
                md=allcols[k]
                columns[k]={str(t):md[t] for t in [1,4,5,7,9,11] if t in md}
            start=min(md.get(11,md[9]) for md in allcols.values())
            end=max(md.get(11,md[9])+md[7] for md in allcols.values())
            groups.append({'number':number,'min':lo.decode(),'max':hi.decode(),'start':start,'end':end,'columns':columns})
        index={'format':1,'revision':revision,'year':year,'optional':optional,'groups':groups}
        atomic_write(cached,gzip.compress(json.dumps(index,separators=(',',':')).encode()))
        return index

    def fetch(self,inn,year=None,as_of=None,revision='main'):
        from .core import validate_inn
        inn=validate_inn(inn);as_of=as_of or date.today()
        if isinstance(as_of,str):as_of=date.fromisoformat(as_of)
        revision=self.resolve_revision(revision)
        if year is None:
            years=[y for y in self.available_years(revision) if date(y,12,31)<as_of]
            if not years:raise SourceError('No completed report year before as_of')
            year=max(years)
        year=int(year)
        if not 2011<=year<=as_of.year or date(year,12,31)>=as_of:raise ValueError('Report period must end before as_of')
        desc=self._descriptor(revision,year)
        available=desc['lastCommit']['date']
        if date.fromisoformat(available[:10])>as_of:
            raise SourceError('Selected source version was published after as_of; select an earlier immutable revision')
        index=self._index(revision,year,desc)
        if index.get('revision')!=revision or index.get('year')!=year:raise SourceError('Cached index identity mismatch')
        url=f'{ROOT_URL}/datasets/{DATASET}/resolve/{revision}/RFSD/year%3D{year}/part-0.parquet'
        matches=[]
        for group in index['groups']:
            if not group['min']<=inn<=group['max']:continue
            a,b=group['start'],group['end']
            raw=self._range(url,a,b-1,self.cache/revision/str(year)/f'group_{group["number"]}_{a}_{b}.bin')
            def read(k):
                md={int(i):v for i,v in group['columns'][k].items()};off=md.get(11,md[9])
                return column(raw[off-a:off-a+md[7]],md,index['optional'][k])
            ids=read('inn');positions=[i for i,v in enumerate(ids) if v==inn]
            if not positions:continue
            data={k:read(k) if k!='inn' else ids for k in FIELDS}
            for i in positions:
                row={k:data[k][i] for k in FIELDS}
                for k in ['creation_date','dissolution_date']:
                    if row[k] is not None:row[k]=(date(1970,1,1)+timedelta(days=int(row[k]))).isoformat()
                matches.append(row)
        if len(matches)>1:raise SourceError('Multiple records for the same INN/year; refusing an ambiguous selection')
        source={'provider':'RFSD','dataset':DATASET,'revision':revision,'report_year':year,
            'available_by':available,'retrieved_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'url':url,'reported_file_sha256':desc.get('lfs',{}).get('oid'),
            'units':'thousand_RUB','license':'CC BY 4.0',
            'attribution':'Bondarkov, Ledenev, Skougarevskiy. Russian Financial Statements Database. doi:10.1038/s41597-025-05150-1'}
        return {'schema_version':'rfsd_snapshot_v1','inn':inn,'year':year,'source':source,'row':matches[0] if matches else None}
