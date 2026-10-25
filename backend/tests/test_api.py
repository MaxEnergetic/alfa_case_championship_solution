from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from fns_parser.api import ApiSettings, create_app
from fns_parser.compact import compact_result
from tests.test_compact import fixture
from tests.test_parser import synthetic_pdf


def full_fixture():
    full = fixture()
    full.update(query_type="inn_legal", started_at="2026-10-20T11:59:55Z")
    return full


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = ApiSettings(download_dir=Path(self.temp.name), max_concurrent=1)
        self.client = self.enterContext(TestClient(create_app(self.settings)))
        self.full = full_fixture()
        self.compact = compact_result(self.full)

    def test_health_docs_and_openapi_without_fns_requests(self):
        with patch("fns_parser.api.parse_company") as parser:
            self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
            self.assertEqual(self.client.get("/docs").status_code, 200)
            schema = self.client.get("/openapi.json").json()
        parser.assert_not_called()
        route = schema["paths"]["/api/v1/companies/{identifier}"]["get"]
        self.assertEqual(set(route["responses"]), {"200", "404", "422", "500", "502", "503"})
        self.assertIn("CompactEntity", schema["components"]["schemas"])

    def test_compact_default_preserves_zero_and_absent_fields(self):
        with patch("fns_parser.api.parse_company", return_value=self.compact) as parser:
            response = self.client.get("/api/v1/companies/4028051108")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.compact)
        self.assertEqual(response.headers["cache-control"], "no-store")
        parser.assert_called_once_with("4028051108", output_dir=self.settings.download_dir,
                                       full=False, timeout=20.0, wait_timeout=60.0, enrich=True, max_pages=3)

    def test_full_result_preserves_unknown_raw_fields(self):
        with patch("fns_parser.api.parse_company", return_value=self.full) as parser:
            response = self.client.get("/api/v1/companies/4028051108?full=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.full)
        self.assertTrue(parser.call_args.kwargs["full"])

    def test_all_identifier_kinds_are_accepted(self):
        with patch("fns_parser.api.parse_company", return_value=self.compact) as parser:
            for identifier in ("4028051108", "784806113663", "1124028002273", "326784700190738"):
                with self.subTest(identifier=identifier):
                    self.assertEqual(self.client.get(f"/api/v1/companies/{identifier}").status_code, 200)
                    self.assertEqual(parser.call_args.args, (identifier,))

    def test_invalid_checksum_and_query_never_reach_parser(self):
        with patch("fns_parser.api.parse_company") as parser:
            for suffix in ("123", "4028051109", "0000000000", "4028051108?full=nonsense",
                           "4028051108?max_pages=0", "4028051108?max_pages=11", "4028051108?enrich=maybe"):
                with self.subTest(suffix=suffix):
                    response = self.client.get("/api/v1/companies/" + suffix)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["status"], "error")
                    self.assertIn("code", response.json()["error"])
        parser.assert_not_called()

    def test_partial_not_found_and_failure_http_statuses(self):
        for status, expected in (("partial", 200), ("not_found", 404), ("error", 502)):
            with self.subTest(status=status):
                result = copy.deepcopy(self.compact)
                result["status"] = status
                if status != "partial":
                    result["entities"] = []
                if status in ("partial", "error"):
                    result["sources"]["transparent_business"] = {
                        "status": "error", "error": {"code": "captcha_required", "message": "captcha"}}
                with patch("fns_parser.api.parse_company", return_value=result):
                    response = self.client.get("/api/v1/companies/4028051108")
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.json(), result)

    def test_server_errors_hide_internal_paths_and_release_slot(self):
        for exception, code in ((OSError("private/server/path"), "file_error"), (RuntimeError("private"), "internal_error")):
            with self.subTest(code=code):
                with patch("fns_parser.api.parse_company", side_effect=exception), self.assertLogs("fns_parser.api", level="ERROR"):
                    response = self.client.get("/api/v1/companies/4028051108")
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertNotIn("private", response.text)
                with patch("fns_parser.api.parse_company", return_value=self.compact):
                    self.assertEqual(self.client.get("/api/v1/companies/4028051108").status_code, 200)

    def test_busy_limit_and_health_stay_responsive(self):
        started, release = Event(), Event()

        def slow_parser(*args, **kwargs):
            started.set()
            if not release.wait(10):
                raise RuntimeError("Test worker deadline exceeded")
            return self.compact

        with patch("fns_parser.api.parse_company", side_effect=slow_parser) as parser, ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.client.get, "/api/v1/companies/4028051108")
            try:
                self.assertTrue(started.wait(3))
                health = pool.submit(self.client.get, "/health").result(timeout=3)
                self.assertEqual(health.status_code, 200)
                busy = pool.submit(self.client.get, "/api/v1/companies/1124028002273").result(timeout=3)
                self.assertEqual(busy.status_code, 503)
                self.assertEqual(busy.headers["retry-after"], "5")
                parser.assert_called_once()
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=3).status_code, 200)

    def test_actual_parser_pipeline_saves_pdf_and_returns_compact(self):
        registry, pb = Mock(), Mock()
        registry.search.return_value = [{"o": "1124028002273", "i": "4028051108", "n": "TEST", "k": "ul"}]
        registry.download.return_value = synthetic_pdf()
        pb.search.return_value = ([{"ogrn": "1124028002273", "inn": "4028051108"}], [])
        pb.card.return_value = {"vyp": {"sschr": 0, "sschr_yearcode": 2025}}
        with patch("fns_parser.service.EgrulClient", return_value=registry), patch("fns_parser.service.TransparentBusinessClient", return_value=pb):
            response = self.client.get("/api/v1/companies/4028051108?enrich=false")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entities"][0]["employees"], {"year": 2025, "count": 0})
        self.assertEqual(len(list(self.settings.download_dir.glob("*.pdf"))), 1)

    def test_cors_only_allows_configured_origins(self):
        app = create_app(ApiSettings(download_dir=Path(self.temp.name), cors_origins=["http://localhost:3000"]))
        with TestClient(app) as client:
            allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
            rejected = client.get("/health", headers={"Origin": "http://example.test"})
        self.assertEqual(allowed.headers["access-control-allow-origin"], "http://localhost:3000")
        self.assertNotIn("access-control-allow-origin", rejected.headers)

    def test_settings_reject_invalid_limits_and_read_environment(self):
        for kwargs in ({"timeout": 0}, {"timeout": float("nan")}, {"wait_timeout": float("inf")}, {"max_concurrent": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                ApiSettings(**kwargs)
        with patch.dict("os.environ", {"FNS_TIMEOUT": "12", "FNS_CORS_ORIGINS": "http://localhost:3000, http://localhost:5173"}):
            settings = ApiSettings.from_env()
        self.assertEqual(settings.timeout, 12)
        self.assertEqual(settings.cors_origins, ["http://localhost:3000", "http://localhost:5173"])


if __name__ == "__main__":
    unittest.main()
