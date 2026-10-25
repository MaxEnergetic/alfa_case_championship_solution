"""Reproducible, unlabeled 10k-company scoring pool from free FNS releases.

Run: python -m fns_parser.dataset_pool
This selects company candidates; it never creates credit-default labels.
The existing company API and its three sources are unchanged.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import http.client
import io
import json
from pathlib import Path
import random
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

from .identifiers import validate_identifier

MSP_URL = "https://file.nalog.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"
FIN_URL = "https://file.nalog.ru/opendata/7707329152-revexp/data-20260925-structure-20180110.zip"
SEED = 20261024
QUOTAS = {"income_gt_expenses": 5000, "expenses_gt_income": 2500, "zero_income": 1000,
          "equal_positive_income_expenses": 500, "financials_not_in_release": 1000}


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def iso(value):
    return datetime.strptime(value, "%d.%m.%Y").date().isoformat()


def rank(value):
    return hashlib.sha256(f"{SEED}:{value}".encode()).hexdigest()


def open_url(request):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


class RemoteZip(io.RawIOBase):
    """Read selected public ZIP members through verified HTTP byte ranges."""
    def __init__(self, url):
        self.url = url
        with open_url(urllib.request.Request(url, method="HEAD")) as response:
            self.size = int(response.headers["Content-Length"])
        self.pos, self.cache_start, self.cache, self.bytes_read = 0, -1, b"", 0
        target = urlsplit(url)
        self.connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=20)
        self.target = target.path + ("?" + target.query if target.query else "")

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        if self.pos < 0:
            raise ValueError("Negative seek")
        return self.pos

    def prefetch(self, start, length):
        end = min(self.size, start + length) - 1
        for attempt in range(3):
            try:
                self.connection.request("GET", self.target, headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
                response = self.connection.getresponse()
                if response.status != 206 or response.getheader("Content-Range") != f"bytes {start}-{end}/{self.size}":
                    self.connection.close()
                    raise ValueError("Server did not honor the requested byte range")
                data = response.read()
                break
            except (OSError, http.client.HTTPException):
                self.connection.close()
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        if len(data) != end - start + 1:
            raise ValueError("Truncated HTTP range")
        self.cache_start, self.cache = start, data
        self.bytes_read += len(data)
        time.sleep(.12)

    def close(self):
        if hasattr(self, "connection"):
            self.connection.close()
        super().close()

    def read(self, n=-1):
        n = self.size - self.pos if n < 0 else min(n, self.size - self.pos)
        if n <= 0: return b""
        if not self.cache_start <= self.pos or self.pos + n > self.cache_start + len(self.cache):
            self.prefetch(self.pos, max(n, 65536))
        offset = self.pos - self.cache_start
        data = self.cache[offset:offset + n]
        self.pos += len(data)
        return data


def sector(okved):
    try: n = int(okved.split(".")[0])
    except (ValueError, AttributeError): return "unknown"
    for low, high, label in [(1,3,"agriculture"),(5,9,"mining"),(10,33,"manufacturing"),
                            (35,39,"utilities"),(41,43,"construction"),(45,47,"trade"),
                            (49,53,"transport"),(55,56,"hospitality"),(58,63,"it_media"),
                            (64,66,"finance"),(68,68,"real_estate"),(69,82,"business_services"),
                            (84,88,"education_health_public"),(90,99,"other_services")]:
        if low <= n <= high: return label
    return "unknown"


def apply_industry(row):
    """Retain both source codes and identify the field used for sampling."""
    basis = ("main_okved" if row.get("main_okved") else
             "reported_main_okved" if row.get("reported_main_okved") else None)
    row["industry_bucket"] = sector(row.get(basis) if basis else None)
    row["industry_bucket_basis"] = basis


def msp_rows(xml, member):
    output, invalid = [], 0
    for doc in ET.fromstring(xml).findall("Документ"):
        entity = doc.find("ОргВклМСП")
        if entity is None: continue  # Exclude individual entrepreneurs from this company pool.
        inn, ogrn = entity.get("ИННЮЛ"), entity.get("ОГРН")
        try:
            if validate_identifier(inn)[1] != "inn_legal" or validate_identifier(ogrn)[1] != "ogrn":
                raise ValueError("Wrong identifier kind")
        except ValueError:
            invalid += 1
            continue
        location = doc.find("СведМН")
        primary = doc.find("СвОКВЭД/СвОКВЭДОсн")
        reported = doc.find("СвОКВЭДотч/СвОКВЭДОсн")
        okved = primary.get("КодОКВЭД") if primary is not None else None
        output.append({"inn": inn, "ogrn": ogrn, "name": entity.get("НаимОргСокр") or entity.get("НаимОрг"),
                       "region_code": location.get("КодРегион") if location is not None else None,
                       "main_okved": okved, "reported_main_okved": reported.get("КодОКВЭД") if reported is not None else None,
                       "industry_bucket": sector(okved), "msp_category_code": doc.get("КатСубМСП"),
                       "new_business_flag_code": doc.get("ПризНовМСП"),
                       "employees": int(doc.get("ССЧР")) if doc.get("ССЧР") is not None else None,
                       "employees_year": 2025, "msp_included_on": iso(doc.get("ДатаВклМСП")),
                       "msp_as_of": iso(doc.get("ДатаСост")), "msp_source_member": member,
                       "msp_source_record": doc.get("ИдДок")})
        apply_industry(output[-1])
    return output, invalid


def load_msp(cache, count):
    remote = RemoteZip(MSP_URL)
    pool, manifests, invalid, conflicts = {}, [], 0, set()
    with zipfile.ZipFile(remote) as archive:
        members = sorted((i for i in archive.infolist() if i.filename.lower().endswith(".xml")), key=lambda i:i.filename)
        chosen = random.Random(SEED).sample(members, count)
        for index, info in enumerate(chosen, 1):
            cached = cache / "msp_members" / (info.filename + ".json")
            if cached.exists(): parsed = json.loads(cached.read_text(encoding="utf-8"))
            else:
                remote.prefetch(info.header_offset, info.compress_size + 4096)
                xml = archive.read(info)  # zipfile verifies the member CRC.
                rows, bad = msp_rows(xml, info.filename)
                parsed = {"rows": rows, "invalid_identifiers": bad, "xml_sha256": hashlib.sha256(xml).hexdigest()}
                write_json(cached, parsed)
            changed = False
            for row in parsed["rows"]:
                previous = (row.get("industry_bucket"), row.get("industry_bucket_basis"))
                apply_industry(row)
                changed |= previous != (row["industry_bucket"], row["industry_bucket_basis"])
            if changed:
                write_json(cached, parsed)
            manifests.append({"member": info.filename, "xml_sha256": parsed["xml_sha256"], "legal_rows": len(parsed["rows"])})
            invalid += parsed["invalid_identifiers"]
            for row in parsed["rows"]:
                inn = row["inn"]
                if inn in pool and pool[inn]["ogrn"] != row["ogrn"]: conflicts.add(inn)
                pool[inn] = row
            if index % 20 == 0 or index == count:
                print(f"MSP members {index}/{count}; candidate companies {len(pool)}; downloaded {remote.bytes_read // 1048576} MiB", flush=True)
    for inn in conflicts: pool.pop(inn, None)
    return pool, {"url":MSP_URL,"snapshot_date":"2026-10-10","archive_bytes":remote.size,
                  "total_members":len(members),"sampled_members":count,"invalid_identifier_rows":invalid,
                  "ambiguous_inns_excluded":len(conflicts),"members":manifests}


def download_financials(cache):
    path = cache / FIN_URL.rsplit("/",1)[-1]
    with open_url(urllib.request.Request(FIN_URL, method="HEAD")) as response:
        size = int(response.headers["Content-Length"])
        expected_hash = response.headers.get("x-amz-meta-sha256")
    if not path.exists() or path.stat().st_size != size:
        cache.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".zip.part")
        downloaded = 0
        with open_url(FIN_URL) as response, partial.open("wb") as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded % (25*1024*1024) == 0:
                    print(f"Financial release downloaded {downloaded//1048576}/{size//1048576} MiB", flush=True)
        if downloaded != size: raise ValueError("Truncated financial release")
        partial.replace(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024*1024): digest.update(chunk)
    if expected_hash and digest.hexdigest() != expected_hash: raise ValueError("Financial archive SHA256 mismatch")
    return path, {"url":FIN_URL,"archive_sha256":digest.hexdigest(),"archive_bytes":size}


def amount(value):
    if value is None: return None
    parsed = Decimal(value)
    if not parsed.is_finite(): raise ValueError("Invalid income/expense value")
    return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)


def financial_case(fin):
    if not fin or fin.get("income_rub") is None or fin.get("expenses_rub") is None: return "financials_not_in_release"
    income, expenses = fin["income_rub"], fin["expenses_rub"]
    if income == 0: return "zero_income"
    if expenses > income: return "expenses_gt_income"
    if income > expenses: return "income_gt_expenses"
    return "equal_positive_income_expenses"


def join_financials(pool, path):
    conflicts, negative_amounts, dates, records = set(), set(), Counter(), 0
    with zipfile.ZipFile(path) as archive:
        members = [i for i in archive.infolist() if i.filename.lower().endswith(".xml")]
        for index, member in enumerate(members, 1):
            for doc in ET.fromstring(archive.read(member)).findall("Документ"):
                records += 1
                entity = doc.find("СведНП")
                inn = entity.get("ИННЮЛ") if entity is not None else None
                if inn not in pool: continue
                values = doc.find("СведДохРасх")
                if values is None: continue
                fin = {"period_end":iso(doc.get("ДатаСост")),"document_date":iso(doc.get("ДатаДок")),
                       "income_rub":amount(values.get("СумДоход")),"expenses_rub":amount(values.get("СумРасход")),
                       "source_member":member.filename,"source_record":doc.get("ИдДок")}
                if any(fin[k] is not None and fin[k] < 0 for k in ("income_rub", "expenses_rub")):
                    negative_amounts.add(inn)
                    continue  # Never silently change signs or label an unusual record as missing.
                existing = pool[inn].get("financials")
                if existing and any(existing.get(k)!=fin[k] for k in ("period_end","income_rub","expenses_rub")): conflicts.add(inn)
                pool[inn]["financials"] = fin
                dates[fin["period_end"]] += 1
            if index % 500 == 0 or index == len(members): print(f"Financial members scanned {index}/{len(members)}", flush=True)
    for inn in conflicts | negative_amounts: del pool[inn]
    return {"documents_scanned":records,"conflicting_inns_excluded":len(conflicts),
            "negative_amount_inns_excluded":len(negative_amounts),"matched_period_counts":dict(dates)}


def select(pool):
    strata = defaultdict(lambda:defaultdict(list))
    for row in pool.values():
        key = (row["region_code"] or "unknown",row["industry_bucket"],row["msp_category_code"] or "unknown")
        strata[financial_case(row.get("financials"))][key].append(row)
    selected, leftovers, actual = [], [], {}
    for case, quota in QUOTAS.items():
        queues = {key:deque(sorted(rows,key=lambda r:rank(r["inn"]))) for key,rows in strata[case].items()}
        keys = sorted(queues,key=lambda key:rank(str(key)))
        chosen = []
        while len(chosen)<quota and any(queues.values()):
            for key in keys:
                if queues[key] and len(chosen)<quota: chosen.append(queues[key].popleft())
        selected.extend(chosen)
        actual[case]=len(chosen)
        leftovers.extend(row for q in queues.values() for row in q)
    deficit=10000-len(selected)
    selected.extend(sorted(leftovers,key=lambda r:rank(r["inn"]))[:deficit])
    if len(selected)!=10000 or len({r["inn"] for r in selected})!=10000 or len({r["ogrn"] for r in selected})!=10000:
        raise ValueError("Cannot produce exactly 10,000 unique legal entities")
    return sorted(selected,key=lambda r:r["inn"]), {"available_cases":{c:sum(map(len,g.values())) for c,g in strata.items()},
                                                   "requested_quotas":QUOTAS,"quotas_before_top_up":actual,"top_up_count":deficit}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members",type=int,default=200)
    parser.add_argument("--output",type=Path,default=Path("output/scoring_pool_10000"))
    parser.add_argument("--cache",type=Path,default=Path("data/scoring_pool_cache"))
    args=parser.parse_args()
    if not 1<=args.members<=7594: parser.error("--members must be between 1 and 7594")
    started=now()
    pool,msp_manifest=load_msp(args.cache,args.members)
    print(f"Candidate base ready: {len(pool)} companies",flush=True)
    financial_path,fin_manifest=download_financials(args.cache)
    fin_manifest.update(join_financials(pool,financial_path))
    selected,sampling=select(pool)
    args.output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for source in selected:
        row=dict(source)
        row.update(financials=row.get("financials"),financial_case=financial_case(row.get("financials")),
                   financial_case_is_credit_label=False,credit_default_90dpd_12m=None,
                   credit_label_status="unavailable_from_public_sources",parser_enrichment_status="pending",source_priority="official_fns_bulk")
        rows.append(row)
    jsonl=args.output/"companies.jsonl"
    jsonl.write_text("".join(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n" for row in rows),encoding="utf-8")
    write_json(args.output/"identifiers.json",[{"inn":r["inn"],"ogrn":r["ogrn"]} for r in rows])
    write_json(args.output/"examples.json",[min((r for r in rows if r["financial_case"]==case), key=lambda r:rank(case+":"+r["inn"])) for case in QUOTAS if any(r["financial_case"]==case for r in rows)])
    stats={"company_count":len(rows),"candidate_count":len(pool),"unique_regions":len({r["region_code"] for r in rows}),
           "cases":dict(Counter(r["financial_case"] for r in rows)),"regions":dict(Counter(r["region_code"] for r in rows)),
           "industries":dict(Counter(r["industry_bucket"] for r in rows)),"msp_category_codes":dict(Counter(r["msp_category_code"] for r in rows)),
           "new_business_flag_codes":dict(Counter(r["new_business_flag_code"] for r in rows)),
           "employees_missing":sum(r["employees"] is None for r in rows),"financials_present":sum(r["financials"] is not None for r in rows)}
    manifest={"schema_version":"scoring-candidate-pool-1.0","started_at":started,"finished_at":now(),"seed":SEED,
              "purpose":"Unlabeled company pool for credit scoring","statistics":stats,"sampling":sampling,
              "sources":{"msp":msp_manifest,"income_expenses":fin_manifest},"companies_jsonl_sha256":hashlib.sha256(jsonl.read_bytes()).hexdigest(),
              "limitations":[
                  "Only legal entities present in the 2026-10-10 MSP snapshot; current membership creates potential survivorship bias for historical default modeling.",
                  "Randomly sampled ZIP members are clusters. Case/region/industry/size balancing is exploratory, not population-representative or probability-calibrated.",
                  "No loan performance/default labels. Income minus expenses is not net profit and is not a loan outcome.",
                  "A missing financial record is not zero or confirmed failure to report.",
                  "Candidates with negative source income/expense amounts are excluded pending interpretation; signs are never silently changed.",
                  "MSP inclusion date is not incorporation date; registry enrichment is needed before age/status analysis.",
                  "EGRUL PDFs, PB cards and GIR BO statements have not been individually collected for these 10,000; enrichment is pending.",
                  "Current facts and later-published financial reports must not become features for an earlier loan decision date.",
                  "Keep related companies and all rows of one INN together for train/test splitting; group linkage is not yet collected."]}
    write_json(args.output/"manifest.json",manifest)
    print(json.dumps(stats,ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__": main()
