"""Human handoff tests use blank synthetic images and mocked upstream responses."""

import base64
from io import BytesIO
import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from fns_parser.api import ApiSettings, create_app
from fns_parser.compact import compact_result
from fns_parser.manual_captcha import fssp_challenge
from fns_parser.public_sources import FsspClient, run_source
from fns_parser.transport import SourceError
from tests.test_api import full_fixture
from tests.test_public_sources import ENTITY, jsonp


def fake_challenge(inn="4028051108", extra=""):
    out = BytesIO()
    Image.new("RGB", (120, 40), "white").save(out, format="PNG")
    src = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    return f'''<div id="captcha-popup"><form id="ncapcha" method="get"
        url="/ajax_search?system=ip&amp;is%5Bvariant%5D=5&amp;is%5Binn%5D={inn}">
        <img id="capchaVisual" src="{src}"><input name="code"></form>{extra}</div>'''


class ChallengeTests(unittest.TestCase):
    def test_extracts_only_verified_raster_image_and_exact_query(self):
        image = fssp_challenge(fake_challenge(extra='<script>alert("untrusted")</script>'), ENTITY["inn"])
        self.assertEqual(image.media_type, "image/png")
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        self.assertIsNone(fssp_challenge("<p>normal results</p>", ENTITY["inn"]))
        for html in (fake_challenge(inn="7707083893"), fake_challenge().replace('/ajax_search?', 'https://evil.test/ajax_search?'),
                     fake_challenge().replace('data:image/png;base64,', 'data:image/svg+xml;base64,')):
            with self.assertRaises(SourceError):
                fssp_challenge(html, ENTITY["inn"])

    def test_same_session_and_inn_are_retained_on_human_submission(self):
        session, human = Mock(), Mock(return_value=("answer", "TEST12"))
        session.fetch.side_effect = [jsonp(fake_challenge()), jsonp("По Вашему запросу ничего не найдено")]
        result = FsspClient(session=session, captcha_handler=human).lookup(ENTITY)
        self.assertEqual(result["total_found"], 0)
        self.assertEqual(session.fetch.call_count, 2)
        query = parse_qs(urlsplit(session.fetch.call_args.args[0]).query)
        self.assertEqual(query["code"], ["TEST12"])
        self.assertEqual(query["is[inn]"], [ENTITY["inn"]])
        self.assertEqual(human.call_args.kwargs["source"], "fssp")
        self.assertNotIn("TEST12", json.dumps(result))

    def test_wrong_answer_shows_new_challenge_and_never_guesses(self):
        session, human = Mock(), Mock(side_effect=[("answer", "BAD"), ("skip", None)])
        session.fetch.side_effect = [jsonp(fake_challenge()), jsonp(fake_challenge())]
        result = run_source("fssp", FsspClient(session=session, captcha_handler=human), ENTITY)
        self.assertEqual(result["error"]["code"], "captcha_skipped")
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual([c.kwargs["attempt"] for c in human.call_args_list], [1, 2])
        self.assertEqual(session.fetch.call_count, 2)

    def test_refresh_only_resends_original_query_and_no_answer(self):
        session, human = Mock(), Mock(side_effect=[("refresh", None), ("skip", None)])
        session.fetch.side_effect = [jsonp(fake_challenge()), jsonp(fake_challenge())]
        run_source("fssp", FsspClient(session=session, captcha_handler=human), ENTITY)
        self.assertEqual(session.fetch.call_args_list[0], session.fetch.call_args_list[1])

    def test_no_handler_never_attempts_an_answer(self):
        session = Mock()
        session.fetch.return_value = jsonp(fake_challenge())
        result = run_source("fssp", FsspClient(session=session), ENTITY)
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual(session.fetch.call_count, 1)


class ManualApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(ApiSettings(download_dir=self.temp.name, max_concurrent=2, captcha_timeout=2))
        self.client = self.enterContext(TestClient(self.app))

    def fake_runner(self, identifier, **kwargs):
        session = Mock()
        session.fetch.side_effect = [jsonp(fake_challenge(inn=identifier)), jsonp("По Вашему запросу ничего не найдено")]
        data = full_fixture()
        source = run_source("fssp", FsspClient(session=session, captcha_handler=kwargs["captcha_handler"]), {**ENTITY, "inn": identifier})
        data["entities"][0]["public_sources"] = {"fssp": source}
        data["sources"]["fssp"] = {"status": source["status"]}
        data["status"] = "ok" if source["status"] == "ok" else "partial"
        return data if kwargs["full"] else compact_result(data)

    def wait_for(self, job_id, state):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/jobs/{job_id}")
            self.assertEqual(response.status_code, 200)
            job = response.json()
            if job["state"] == state:
                return job
            time.sleep(.01)
        self.fail(f"Job did not reach {state}")

    def start(self, inn="4028051108"):
        response = self.client.post("/api/v1/jobs", json={"identifier": inn})
        self.assertEqual(response.status_code, 202)
        return self.wait_for(response.json()["job_id"], "waiting_for_captcha")

    def test_complete_server_handoff_keeps_code_out_of_status(self):
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            job = self.start()
            c = job["captcha"]
            image = self.client.get(c["image_url"])
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.headers["content-type"], "image/png")
            self.assertEqual(image.headers["cache-control"], "no-store")
            self.assertEqual(self.client.get("/health").status_code, 200)
            reply = self.client.post(c["answer_url"], json={"code": "TEST12"})
            self.assertEqual(reply.status_code, 200)
            done = self.wait_for(job["job_id"], "completed")
            self.assertEqual(done["result"]["entities"][0]["public_sources"]["fssp"]["total_found"], 0)
            self.assertNotIn("TEST12", json.dumps(done))
            self.assertNotIn("captcha", done)
            self.assertEqual(self.client.get(c["image_url"]).status_code, 409)
            self.assertEqual(self.client.post(c["answer_url"], json={"code": "TEST12"}).status_code, 409)

    def test_two_jobs_cannot_share_a_challenge(self):
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            first, second = self.start(), self.start("7707083893")
            wrong = f"/api/v1/jobs/{second['job_id']}/captchas/{first['captcha']['id']}/answer"
            self.assertEqual(self.client.post(wrong, json={"code": "TEST12"}).status_code, 409)
            for job in (first, second):
                self.client.post(job["captcha"]["answer_url"], json={"action": "skip"})
                result = self.wait_for(job["job_id"], "completed")["result"]
                self.assertEqual(result["status"], "partial")
                self.assertNotIn("total_found", result["entities"][0]["public_sources"]["fssp"])

    def test_cancel_releases_waiter_and_old_image(self):
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            job = self.start()
            response = self.client.delete(job["status_url"])
            self.assertEqual(response.json()["state"], "cancelled")
            self.assertEqual(self.client.get(job["captcha"]["image_url"]).status_code, 409)

    def test_timeout_completes_partial_without_zero_and_releases_slot(self):
        self.app.state.jobs.captcha_timeout = .05
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            response = self.client.post("/api/v1/jobs", json={"identifier": "4028051108"})
            done = self.wait_for(response.json()["job_id"], "completed")
            entry = done["result"]["entities"][0]["public_sources"]["fssp"]
            self.assertEqual(entry["error"]["code"], "captcha_timeout")
            self.assertNotIn("total_found", entry)
            self.assertTrue(self.app.state.jobs.slots.acquire(blocking=False))
            self.app.state.jobs.slots.release()

    def test_invalid_answer_cross_origin_and_extra_fields(self):
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            job = self.start()
            for payload in ({}, {"code": "<script>"}, {"action": "skip", "code": "123"}, {"code": "123", "url": "https://evil.test"}):
                self.assertEqual(self.client.post(job["captcha"]["answer_url"], json=payload).status_code, 422)
            denied = self.client.post(job["captcha"]["answer_url"], json={"code": "123"}, headers={"Origin": "https://evil.test"})
            self.assertEqual(denied.status_code, 403)
            self.client.post(job["captcha"]["answer_url"], json={"action": "skip"})
            self.wait_for(job["job_id"], "completed")

    def test_limits_shared_with_sync_route_and_no_global_listing(self):
        with patch("fns_parser.api.parse_company", side_effect=self.fake_runner):
            first, second = self.start(), self.start()
            for method, url, kwargs in (("post", "/api/v1/jobs", {"json": {"identifier": "4028051108"}}),
                                        ("get", "/api/v1/companies/4028051108", {})):
                response = getattr(self.client, method)(url, **kwargs)
                self.assertEqual(response.status_code, 503)
            self.assertEqual(self.client.get("/api/v1/jobs/unknown").status_code, 404)
            self.assertEqual(self.client.get("/api/v1/jobs").status_code, 405)
            self.client.delete(first["status_url"])
            self.client.delete(second["status_url"])

    def test_ui_assets_and_unknown_input_do_not_call_upstream(self):
        with patch("fns_parser.api.parse_company") as parser:
            page = self.client.get("/captcha")
            self.assertEqual(page.status_code, 200)
            self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
            self.assertIn("captcha.js", page.text)
            self.assertEqual(self.client.get("/captcha/assets/captcha.js").status_code, 200)
            self.assertEqual(self.client.get("/captcha/assets/private.py").status_code, 422)
            self.assertEqual(self.client.post("/api/v1/jobs", json={"identifier": "4028051109"}).status_code, 422)
            parser.assert_not_called()

    def test_finished_jobs_expire_and_worker_errors_release_capacity(self):
        self.app.state.jobs.retention = .02
        with patch("fns_parser.api.parse_company", side_effect=RuntimeError("private path")), self.assertLogs("fns_parser.jobs", level="ERROR"):
            job = self.client.post("/api/v1/jobs", json={"identifier": "4028051108"}).json()
            failed = self.wait_for(job["job_id"], "failed")
        self.assertNotIn("private", json.dumps(failed))
        self.assertTrue(self.app.state.jobs.slots.acquire(blocking=False))
        self.app.state.jobs.slots.release()
        time.sleep(.03)
        self.assertEqual(self.client.get(job["status_url"]).status_code, 404)


if __name__ == "__main__":
    unittest.main()
