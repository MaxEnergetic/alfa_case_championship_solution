import io
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
import sys
import threading
from unittest.mock import Mock, patch
import urllib.error

from fns_parser.extract import parse_pdf
from fns_parser.identifiers import validate_identifier
from fns_parser.providers import EgrulClient, TransparentBusinessClient
from fns_parser.service import parse_company
from fns_parser.transport import Session, SourceError, check_payload, poll


def synthetic_pdf() -> bytes:
    """Two-page real PDF with a digit grid, repeated labels and a split row.

    Pure Python PDF fixture so tests require no network, personal data or
    additional document-generation dependencies.
    """
    def text(x, y, value):
        return f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET\n"

    def table(ys):
        return ("0.5 w\n" + "".join(f"{x} {min(ys)} m {x} {max(ys)} l S\n" for x in (30, 60, 260, 560))
                + "".join(f"30 {y} m 560 {y} l S\n" for y in ys))

    first = "0.5 w\n30 760 m 212 760 l S\n30 780 m 212 780 l S\n"
    for i in range(14):
        first += f"{30+i*14} 760 m {30+i*14} 780 l S\n"
        if i < 13:
            first += text(33+i*14, 765, str(i % 10))
    first += table((700, 675, 650))
    first += text(35, 682, "1") + text(65, 682, "Name") + text(265, 682, "FIRST COMPANY")
    first += text(35, 657, "2") + text(65, 657, "Long label") + text(265, 657, "first part")
    second = table((700, 675, 650))
    second += text(65, 682, "continued") + text(265, 682, "second part")
    second += text(35, 657, "3") + text(65, 657, "Name") + text(265, 657, "SECOND COMPANY")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for content in (first, second):
        raw = content.encode("ascii")
        objects.append(f"<< /Length {len(raw)} >>\nstream\n".encode() + raw + b"endstream")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)


class IdentifierTests(unittest.TestCase):
    def test_four_identifier_formats_and_whitespace(self):
        for value, kind in [("4028051108", "inn_legal"), ("784806113663", "inn_individual"),
                            ("1124028002273", "ogrn"), ("326784700190738", "ogrnip")]:
            with self.subTest(value=value):
                self.assertEqual(validate_identifier(" " + value + "\n"), (value, kind))

    def test_invalid_inputs_are_rejected(self):
        for value in ["0000000000", "4028051109", "784806113664", "1124028002274", "326784700190739",
                      "123", "4O28051108", "４０２８０５１１０８", 4028051108]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_identifier(value)


class PdfTests(unittest.TestCase):
    def test_registry_subheadings_do_not_pollute_previous_field(self):
        page = Mock()
        page.extract_text.return_value = "Registry"
        page.extract_tables.return_value = [[
            ["Сведения об основном виде деятельности", None, None],
            ["1", "Код", "62.01"],
            ["Сведения о дополнительных видах деятельности", None, None],
            ["", "(ОКВЭД ОК 029-2014)", None],
            ["2", "Код", "62.02"],
            ["", "Сведения о документах, представленных при внесении записи", ""],
            ["3", "Наименование документа", "Документ"],
        ]]
        with patch("fns_parser.extract.pdfplumber.open") as opened:
            opened.return_value.__enter__.return_value.pages = [page]
            result = parse_pdf(b"fixture")
        self.assertEqual(result["fields"][0]["label"], "Код")
        self.assertEqual(result["fields"][1]["label"], "Код")
        self.assertEqual(result["fields"][1]["subsection"], "(ОКВЭД ОК 029-2014)")
        self.assertEqual(result["fields"][2]["subsection"], "Сведения о документах, представленных при внесении записи")

    def test_real_pdf_ignores_digit_grid_and_merges_page_continuation(self):
        data = parse_pdf(synthetic_pdf())
        self.assertEqual(data["page_count"], 2)
        self.assertEqual([f["number"] for f in data["fields"]], [1, 2, 3])
        self.assertEqual(data["fields"][1]["label"], "Long label continued")
        self.assertEqual(data["fields"][1]["value"], "first part second part")
        self.assertEqual(data["fields"][1]["page_end"], 2)
        self.assertEqual([f["value"] for f in data["fields"] if f["label"] == "Name"],
                         ["FIRST COMPANY", "SECOND COMPANY"])
        self.assertEqual(data["warnings"], [])
        self.assertGreater(len(data["pages"][0]["tables"]), 1)

    def test_invalid_pdf(self):
        with self.assertRaises(SourceError) as ctx:
            parse_pdf(b"not a PDF")
        self.assertEqual(ctx.exception.code, "pdf_parse_error")


class ProtocolTests(unittest.TestCase):
    def test_not_found_egrul_certificate_is_not_a_company(self):
        session = Mock()
        session.json.side_effect = [{"t": "search-job"}, {"rows": [{"cnt": "0", "t": "certificate"}]}]
        self.assertEqual(EgrulClient(session).search("4028051108"), [])

    def test_pb_pagination_and_exact_identifier_match(self):
        session = Mock()
        row = {"ogrn": "326784700190738", "inn": "784806113663", "token": "t"}
        old = {**row, "ogrn": "316784700262702"}
        session.json.side_effect = [
            {"id": "page-one"}, {"ip": {"data": [row], "rowCount": 2, "pageSize": 1, "hasMore": True}},
            {"id": "page-two"}, {"ip": {"data": [old], "rowCount": 2, "pageSize": 1, "hasMore": False}},
        ]
        rows, pages = TransparentBusinessClient(session).search("784806113663")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(pages), 2)

    def test_pb_rejects_card_for_different_registration(self):
        session = Mock()
        session.json.side_effect = [{"id": "job", "token": "poll-token"}, {"vyp": {"ОГРНИП": "316784700262702"}}]
        with self.assertRaises(SourceError) as ctx:
            TransparentBusinessClient(session).card({"token": "t", "ogrn": "326784700190738"})
        self.assertEqual(ctx.exception.code, "identity_mismatch")

    def test_captcha_in_http_error_is_reported_without_retry(self):
        session = Session("https://pb.nalog.ru/", min_interval=0)
        body = json.dumps({"ERRORS": {"pbSearchCaptcha": ["Required"]}}).encode()
        session.opener = Mock()
        session.opener.open.side_effect = urllib.error.HTTPError("https://pb.nalog.ru/", 400, "Bad request", {}, io.BytesIO(body))
        with self.assertRaises(SourceError) as ctx:
            session.json("search-proc.json", {"queryUl": "4028051108"})
        self.assertEqual(ctx.exception.code, "captcha_required")
        self.assertEqual(session.opener.open.call_count, 1)

    def test_rate_limit_and_upstream_errors(self):
        for payload, code in [({"ERRORS": {"pbRateLimit": ["limit"]}}, "rate_limited"),
                              ({"captchaRequired": True}, "captcha_required"),
                              ({"ERROR": "broken"}, "upstream_error")]:
            with self.subTest(code=code), self.assertRaises(SourceError) as ctx:
                check_payload(payload)
            self.assertEqual(ctx.exception.code, code)

    def test_poll_has_deadline(self):
        with self.assertRaises(SourceError) as ctx:
            poll(lambda: None, lambda x: x is not None, wait_timeout=0.001, interval=0.001)
        self.assertEqual(ctx.exception.code, "poll_timeout")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.egrul, self.pb = Mock(), Mock()
        self.row = {"o": "1124028002273", "i": "4028051108", "n": "SYNTHETIC COMPANY", "k": "ul", "t": "t"}
        self.pbrow = {"ogrn": self.row["o"], "inn": self.row["i"], "token": "t"}
        self.egrul.search.return_value = [self.row]
        self.egrul.download.return_value = synthetic_pdf()
        self.pb.search.return_value = ([self.pbrow], [])
        self.pb.card.return_value = {"vyp": {"ИНН": "4028051108", "ОГРН": "1124028002273", "sschr": 0},
                                     "custom_future_section": {"anything": [1, 2, 3]}}

    def run_parser(self, query="4028051108"):
        return parse_company(query, output_dir=self.temp.name, full=True, enrich=False,
                             egrul_client=self.egrul, pb_client=self.pb)

    def test_full_result_preserves_unrecognized_sections_and_zero(self):
        result = self.run_parser()
        self.assertEqual(result["status"], "ok")
        entity = result["entities"][0]
        self.assertTrue(Path(entity["egrul"]["pdf"]["path"]).is_file())
        self.assertEqual(entity["summary"]["employees"]["count"], 0)
        self.assertIsNone(entity["summary"]["income_expenses"]["income"])
        self.assertEqual(entity["transparent_business"]["data"]["custom_future_section"], {"anything": [1, 2, 3]})
        json.dumps(result, allow_nan=False)

    def test_default_returns_compact_and_still_saves_pdf(self):
        result = parse_company("4028051108", output_dir=self.temp.name, enrich=False,
                               egrul_client=self.egrul, pb_client=self.pb)
        self.assertEqual(result["schema_version"], "compact-1.0")
        self.assertNotIn("egrul", result["entities"][0])
        self.assertEqual(len(list(Path(self.temp.name).glob("*.pdf"))), 1)

    def test_pb_failure_keeps_registry_pdf(self):
        self.pb.search.side_effect = SourceError("network_error", "offline")
        result = self.run_parser()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["entities"][0]["egrul"]["status"], "ok")
        self.assertEqual(result["entities"][0]["transparent_business"]["status"], "error")

    def test_download_failure_keeps_pb_and_registry_search(self):
        self.egrul.download.side_effect = SourceError("captcha_required", "captcha")
        result = self.run_parser()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["entities"][0]["egrul"]["search_result"], self.row)
        self.assertIn("data", result["entities"][0]["transparent_business"])

    def test_corrupt_pdf_remains_downloadable_and_is_not_success(self):
        self.egrul.download.return_value = b"%PDF-broken"
        result = self.run_parser()
        self.assertEqual(result["status"], "partial")
        entry = result["entities"][0]["egrul"]
        self.assertTrue(Path(entry["pdf"]["path"]).exists())
        self.assertEqual(entry["error"]["code"], "pdf_parse_error")

    def test_both_not_found_vs_both_failed(self):
        self.egrul.search.return_value = []
        self.pb.search.return_value = ([], [])
        self.assertEqual(self.run_parser()["status"], "not_found")
        self.egrul.search.side_effect = SourceError("network_error", "offline")
        self.pb.search.side_effect = SourceError("network_error", "offline")
        self.assertEqual(self.run_parser()["status"], "error")

    def test_multiple_ip_registrations_are_separate(self):
        ids = ["316784700262702", "326784700190738"]
        self.egrul.search.return_value = [{"o": oid, "i": "784806113663", "k": "fl", "t": "t"} for oid in ids]
        self.pb.search.return_value = ([{"ogrn": oid, "inn": "784806113663", "token": "t"} for oid in ids], [])
        self.pb.card.side_effect = lambda row: {"vyp": {"ОГРНИП": row["ogrn"]}}
        result = self.run_parser("784806113663")
        self.assertEqual([e["ogrn"] for e in result["entities"]], ids)
        self.assertEqual(len(list(Path(self.temp.name).glob("egrip_*.pdf"))), 2)

    def test_bad_identifier_never_calls_sources(self):
        with self.assertRaises(ValueError):
            self.run_parser("123")
        self.egrul.search.assert_not_called()
        self.pb.search.assert_not_called()

    def test_parallel_sources_use_separate_workers_and_merge_both_results(self):
        barrier = threading.Barrier(2)
        def registry_search(query):
            barrier.wait(timeout=3)
            return [self.row]
        def pb_search(query):
            barrier.wait(timeout=3)
            return [self.pbrow], []
        self.egrul.search.side_effect = registry_search
        self.pb.search.side_effect = pb_search
        result = parse_company(self.row['o'], output_dir=self.temp.name, full=True, enrich=False,
                               egrul_client=self.egrul, pb_client=self.pb, parallel_sources=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['entities']), 1)
        self.assertIn('extract', result['entities'][0]['egrul'])
        self.assertIn('data', result['entities'][0]['transparent_business'])

    def test_retry_only_failed_source_retains_pdf_dates_and_public_data(self):
        public = Mock()
        public.lookup.return_value = {'status': 'ok', 'years': [{'year': 2025, 'assets_rub': 0}]}
        self.pb.search.side_effect = SourceError('network_error', 'temporary')
        options = dict(output_dir=self.temp.name, full=True, egrul_client=self.egrul,
                       pb_client=self.pb, public_clients={'girbo': public}, parallel_sources=True)
        first = parse_company(self.row['o'], **options)
        before = copy.deepcopy(first)
        self.pb.search.side_effect = None
        second = parse_company(self.row['o'], previous_result=first, **options)
        self.assertEqual(second['status'], 'ok')
        self.egrul.search.assert_called_once()
        self.egrul.download.assert_called_once()
        self.assertEqual(self.pb.search.call_count, 2)
        public.lookup.assert_called_once()
        old, new = first['entities'][0], second['entities'][0]
        self.assertEqual(old['egrul']['pdf'], new['egrul']['pdf'])
        self.assertEqual(old['egrul']['fetched_at'], new['egrul']['fetched_at'])
        self.assertEqual(old['public_sources']['girbo']['checked_at'], new['public_sources']['girbo']['checked_at'])
        self.assertTrue(new['egrul']['cache_reused'])
        self.assertTrue(new['public_sources']['girbo']['cache_reused'])
        self.assertNotIn('cache_reused', new['transparent_business'])
        self.assertEqual(first, before)
        self.assertEqual(len(list(Path(self.temp.name).glob('*.pdf'))), 1)

    def test_retry_registry_reuses_successful_pb_even_when_girbo_failed(self):
        public = Mock()
        public.lookup.side_effect = SourceError('network_error', 'temporary')
        self.egrul.search.side_effect = SourceError('network_error', 'temporary')
        options = dict(output_dir=self.temp.name, full=True, egrul_client=self.egrul,
                       pb_client=self.pb, public_clients={'girbo': public})
        first = parse_company(self.row['o'], **options)
        self.egrul.search.side_effect = None
        public.lookup.side_effect = None
        public.lookup.return_value = {'status': 'ok', 'years': []}
        second = parse_company(self.row['o'], previous_result=first, **options)
        self.assertEqual(second['status'], 'ok')
        self.pb.search.assert_called_once()
        self.pb.card.assert_called_once()
        self.assertEqual(public.lookup.call_count, 2)
        self.assertEqual(self.egrul.search.call_count, 2)

    def test_expired_and_damaged_pdf_are_refetched_without_refetching_pb(self):
        options = dict(output_dir=self.temp.name, full=True, enrich=False,
                       egrul_client=self.egrul, pb_client=self.pb)
        first = parse_company(self.row['o'], **options)
        stale = copy.deepcopy(first)
        stale['entities'][0]['egrul']['fetched_at'] = (datetime.now(timezone.utc)-timedelta(hours=7)).isoformat()
        parse_company(self.row['o'], previous_result=stale, **options)
        Path(first['entities'][0]['egrul']['pdf']['path']).write_bytes(b'damaged')
        parse_company(self.row['o'], previous_result=first, **options)
        self.assertEqual(self.egrul.search.call_count, 3)
        self.pb.search.assert_called_once()

    def test_cached_result_for_different_query_and_inn_is_not_reused(self):
        options = dict(output_dir=self.temp.name, full=True, enrich=False,
                       egrul_client=self.egrul, pb_client=self.pb)
        first = parse_company(self.row['o'], **options)
        wrong = copy.deepcopy(first)
        wrong['query'] = '1060224012155'
        parse_company(self.row['o'], previous_result=wrong, **options)
        inn_result = parse_company(self.row['i'], **options)
        parse_company(self.row['i'], previous_result=inn_result, **options)
        self.assertEqual(self.egrul.search.call_count, 4)
        self.assertEqual(self.pb.search.call_count, 4)


class CliTests(unittest.TestCase):
    def test_help_and_error_are_utf8_even_under_windows_legacy_encoding(self):
        env = {**os.environ, "PYTHONIOENCODING": "cp1251"}
        for arguments, expected_code in [(["--help"], 0), (["123"], 1)]:
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, "-m", "fns_parser", *arguments],
                                        capture_output=True, env=env, check=False)
                self.assertEqual(result.returncode, expected_code, result.stderr.decode("utf-8"))
                text = result.stdout.decode("utf-8")
                if arguments == ["123"]:
                    self.assertEqual(json.loads(text)["status"], "error")
                else:
                    self.assertIn("ИНН", text)


if __name__ == "__main__":
    unittest.main()
