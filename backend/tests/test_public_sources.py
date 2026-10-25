"""Offline contract/safety tests. Synthetic HTML is not a successful live check."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from fastapi.testclient import TestClient

from fns_parser.api import ApiSettings, create_app
from fns_parser.compact import compact_result
from fns_parser.public_sources import (
    ArbitrationClient, FsspClient, GirboClient, ManualRegistryClient, bfo_summary,
    enrich_result, parse_arbitration, parse_fssp, run_source,
)
from fns_parser.service import parse_company
from fns_parser.transport import Session, SourceError
from tests.test_api import full_fixture
from tests.test_parser import synthetic_pdf

ENTITY = {"inn": "4028051108", "ogrn": "1124028002273", "entity_type": "legal"}


def kad_page(total, case_id=1):
    return (f'<input id="documentsTotalCount" value="{total}">' + (f'''
        <table><tbody><tr><td class="num"><span>12.01.2025</span>
        <a href="/Card/00000000-0000-0000-0000-{case_id:012}">А23-{case_id}/2025</a></td>
        <td class="court">АС Калужской области</td><td class="plaintiff">Истец ИНН 4028051108</td>
        <td class="respondent">Ответчик</td></tr></tbody></table>''' if total else ""))


def jsonp(html):
    return ("/**/ typeof fnsCallback === 'function' && fnsCallback("
            + json.dumps({"data": html, "err": None, "e": ""}) + ");").encode()


class GirboTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / "fixtures/girbo_4028051108.json").read_text("utf-8"))
        self.session = Mock()
        self.session.fetch.side_effect = [json.dumps(self.fixture[k]).encode() for k in ("search", "organization", "reports")]
        self.client = GirboClient(session=self.session)

    def test_real_statement_excerpt_units_missing_and_zero(self):
        result = self.client.lookup(ENTITY)
        self.assertEqual(result["status"], "ok")
        years = {row["year"]: row for row in result["years"]}
        self.assertEqual(years[2024]["revenue_rub"], 170000)
        self.assertEqual(years[2024]["net_profit_rub"], 36000)
        self.assertEqual(years[2024]["accounts_payable_rub"], 71000)
        self.assertEqual(years[2025]["assets_rub"], 0)
        self.assertNotIn("revenue_rub", years[2025])
        self.assertNotIn("borrowings_rub", years[2024])
        self.assertEqual(result["raw"]["reports"], self.fixture["reports"])

    def test_card_identity_is_checked_before_reports(self):
        wrong = {**self.fixture["organization"], "ogrn": "1027700132195"}
        self.session.fetch.side_effect = [json.dumps(x).encode() for x in (self.fixture["search"], wrong)]
        with self.assertRaises(SourceError) as ctx:
            self.client.lookup(ENTITY)
        self.assertEqual(ctx.exception.code, "identity_mismatch")
        self.assertEqual(self.session.fetch.call_count, 2)

    def test_report_identity_and_exact_money_total(self):
        report = {"period": "2024", "organizationInfo": {"inn": ENTITY["inn"]}, "typeCorrections": [
            {"type": 12, "correction": {"balance": {"current1410": "0.1", "current1510": "0.2"}}}]}
        self.assertEqual(bfo_summary([report], ENTITY["inn"])[0]["borrowings_rub"], 300)
        report["organizationInfo"]["inn"] = "7707083893"
        with self.assertRaises(SourceError):
            bfo_summary([report], ENTITY["inn"])

    def test_report_failure_keeps_verified_company_card(self):
        self.session.fetch.side_effect = [json.dumps(self.fixture[k]).encode() for k in ("search", "organization")] + [SourceError("network_error", "offline")]
        result = self.client.lookup(ENTITY)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["raw"]["organization"]["inn"], ENTITY["inn"])
        self.assertNotIn("years", result)

    def test_individual_no_network_and_not_found_is_explicit(self):
        result = self.client.lookup({**ENTITY, "entity_type": "individual"})
        self.assertEqual(result["status"], "not_applicable")
        self.session.fetch.assert_not_called()
        self.session.fetch.side_effect = None
        self.session.fetch.return_value = b'{"content":[],"last":true,"totalPages":0}'
        self.assertEqual(self.client.lookup(ENTITY)["status"], "not_found")

    def test_search_limit_is_not_not_found(self):
        self.session.fetch.side_effect = None
        self.session.fetch.return_value = b'{"content":[],"last":false,"totalPages":50}'
        result = run_source("girbo", GirboClient(session=self.session, max_pages=1), ENTITY)
        self.assertEqual(result["error"]["code"], "search_limit")


class RegistryTests(unittest.TestCase):
    def test_kad_positive_zero_and_unknown_html(self):
        total, cases = parse_arbitration(kad_page(1))
        self.assertEqual(total, 1)
        self.assertEqual(cases[0]["registered_on"], "2025-01-12")
        self.assertEqual(cases[0]["number"], "А23-1/2025")
        self.assertEqual(parse_arbitration(kad_page(0)), (0, []))
        for html in ("<h1>Maintenance</h1>", '<input id="documentsTotalCount" value="2">'):
            with self.assertRaises(SourceError):
                parse_arbitration(html)

    def test_kad_pagination_limit_and_second_page_failure(self):
        for response in (kad_page(3, 2).encode(), SourceError("captcha_required", "captcha")):
            session = Mock()
            session.fetch.side_effect = [b"homepage", kad_page(3).encode(), response]
            result = ArbitrationClient(session=session, max_pages=2).lookup(ENTITY)
            self.assertEqual(result["status"], "partial")
            self.assertTrue(result["truncated"])
            self.assertEqual(result["total_cases"], 3)
            self.assertEqual(result["cases"][0]["number"], "А23-1/2025")
            self.assertEqual(session.fetch.call_args.kwargs["json_body"]["Sides"],
                             [{"Name": ENTITY["inn"], "Type": -1, "ExactMatch": True}])

    def test_kad_duplicate_page_is_not_complete(self):
        session = Mock()
        session.fetch.side_effect = [b"homepage", kad_page(2).encode(), kad_page(2).encode()]
        result = ArbitrationClient(session=session, max_pages=2).lookup(ENTITY)
        self.assertEqual(result["error"]["code"], "pagination_stalled")
        self.assertEqual(len(result["cases"]), 1)

    def test_fssp_captcha_is_not_an_empty_result(self):
        session = Mock()
        session.fetch.return_value = jsonp('<div id="captcha-popup"><form id="ncapcha">Введите код</form></div>')
        result = run_source("fssp", FsspClient(session=session), ENTITY)
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual(result["error"]["code"], "captcha_required")
        self.assertNotIn("proceedings", result)
        self.assertNotIn("total_found", result)
        self.assertEqual(session.fetch.call_count, 1)

    def test_fssp_jsonp_no_eval_and_zero_only_when_confirmed(self):
        result = parse_fssp(jsonp('<p>По Вашему запросу ничего не найдено</p>'))
        self.assertEqual(result["total_found"], 0)
        for content in (b"fnsCallback({}); evil();", jsonp("<h1>Bad gateway</h1>"), b"not JSON"):
            with self.assertRaises(SourceError):
                parse_fssp(content)

    def test_fssp_table_cannot_claim_complete_coverage(self):
        html = '''<table><tr><th>Должник</th><th>Исполнительное производство</th><th>Предмет исполнения</th></tr>
        <tr><td>Тест</td><td>123/25/40001-ИП от 01.01.2025</td>
        <td>Иные взыскания. Сумма долга: 1 200,50 руб. Остаток долга: 0,00 руб.</td></tr></table>'''
        result = parse_fssp(jsonp(html))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["proceedings"][0]["debt_rub"], 1200.5)
        self.assertEqual(result["proceedings"][0]["remaining_debt_rub"], 0)
        self.assertNotIn("total_found", result)

    def test_manual_registry_never_fakes_a_search(self):
        session = Mock()
        result = run_source("pledges", ManualRegistryClient("pledges", session=session), ENTITY)
        session.fetch.assert_not_called()
        self.assertFalse(result["lookup_performed"])
        self.assertEqual(result["status"], "manual_required")
        session.fetch.return_value = b"homepage"
        result = run_source("fedresurs", ManualRegistryClient("fedresurs", session=session), ENTITY)
        self.assertFalse(result["lookup_performed"])
        self.assertEqual(result["error"]["code"], "browser_required")

    def test_challenge_and_access_restriction_never_retried(self):
        for code, body, expected in ((401, b"<script>qauth_show_captcha()</script>", "captcha_required"),
                                      (451, b"Restricted", "access_restricted")):
            session = Session("https://kad.arbitr.ru/", retries=2, min_interval=0)
            session.opener = Mock()
            session.opener.open.side_effect = urllib.error.HTTPError(session.base_url, code, "Denied", {}, io.BytesIO(body))
            with self.assertRaises(SourceError) as ctx:
                session.fetch("/")
            self.assertEqual(ctx.exception.code, expected)
            self.assertEqual(session.opener.open.call_count, 1)


class EnrichmentTests(unittest.TestCase):
    def test_isolated_failures_and_compact_raw_omission(self):
        full = full_fixture()
        original = copy.deepcopy(full["entities"][0])
        clients = {"girbo": Mock(), "fssp": Mock()}
        clients["girbo"].lookup.return_value = {"status": "ok", "years": [{"year": 2025, "assets_rub": 0}], "raw": {"big": "data"}}
        clients["fssp"].lookup.side_effect = SourceError("captcha_required", "captcha")
        enrich_result(full, clients=clients)
        self.assertEqual(full["status"], "partial")
        self.assertEqual(full["entities"][0]["egrul"], original["egrul"])
        compact = compact_result(full)
        data = compact["entities"][0]["public_sources"]
        self.assertNotIn("raw", data["girbo"])
        self.assertEqual(data["girbo"]["years"][0]["assets_rub"], 0)
        self.assertNotIn("total_found", data["fssp"])
        with TestClient(create_app()) as client, patch("fns_parser.api.parse_company", return_value=compact):
            response = client.get("/api/v1/companies/4028051108")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), compact)

    def test_shared_inn_lookup_keeps_registrations_and_scope(self):
        full = full_fixture()
        full["entities"] = [{"inn": "784806113663", "ogrn": ogrn, "entity_type": "individual"}
                            for ogrn in ("316784700262702", "326784700190738")]
        client = Mock()
        client.lookup.return_value = {"status": "ok", "query_scope": "inn", "query_inn": "784806113663", "total_cases": 0, "cases": []}
        enrich_result(full, clients={"arbitration": client})
        client.lookup.assert_called_once()
        self.assertEqual(len(full["entities"]), 2)
        self.assertEqual(full["entities"][0]["public_sources"], full["entities"][1]["public_sources"])

    def test_captcha_stops_further_registrations_without_copying_data(self):
        full = full_fixture()
        full["entities"].append({**ENTITY, "ogrn": "1027700132195"})
        client = Mock()
        client.lookup.return_value = {"status": "partial", "raw": {"one_registration": True},
                                      "error": {"code": "captcha_required", "message": "captcha"}}
        enrich_result(full, clients={"girbo": client})
        client.lookup.assert_called_once()
        second = full["entities"][1]["public_sources"]["girbo"]
        self.assertEqual(second["status"], "manual_required")
        self.assertNotIn("raw", second)
        self.assertFalse(second["lookup_performed"])

    def test_enrichment_is_default_but_basic_does_not_create_clients(self):
        registry, pb = Mock(), Mock()
        registry.search.return_value = [{"o": ENTITY["ogrn"], "i": ENTITY["inn"], "n": "TEST", "k": "ul"}]
        registry.download.return_value = synthetic_pdf()
        pb.search.return_value = ([], [])
        source = Mock()
        source.lookup.return_value = {"status": "ok", "years": [{"year": 2025, "assets_rub": 0}]}
        with tempfile.TemporaryDirectory() as tmp:
            enriched = parse_company(ENTITY["inn"], output_dir=tmp, egrul_client=registry, pb_client=pb,
                                     public_clients={"girbo": source})
            self.assertIn("girbo", enriched["entities"][0]["public_sources"])
            with patch("fns_parser.public_sources.default_clients") as factory:
                basic = parse_company(ENTITY["inn"], output_dir=tmp, enrich=False, egrul_client=registry, pb_client=pb)
            factory.assert_not_called()
            self.assertNotIn("public_sources", basic["entities"][0])


if __name__ == "__main__":
    unittest.main()
