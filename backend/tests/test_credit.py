import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from fns_parser.api import ApiSettings, create_app
from fns_parser.credit.dialogue import submit
from fns_parser.credit.questions import CreditError
from fns_parser.credit.rules import recommend
from fns_parser.credit.store import SessionStore
from fns_parser.public_sources import default_clients


BASE = dict(knowledge="no", goal="gap", gap_days=45, receivable="no", registered="yes", form="ooo",
            inn="4028051108", alpha_client="no", amount=1_000_000, needed_date="2099-01-15",
            repayment_source="revenue", guarantor="yes", guarantor_details="Собственник, доля 100%",
            seasonal="no", debt_payments=0, arrears="no", regular="no", disbursement="once",
            reuse="no", term_months=24, tranche_months=3, collateral="no", asset="equipment",
            own_funds="no", property_chosen="yes", asset_cost=3_000_000, vehicle="Новый грузовой автомобиль",
            existing_loan="yes", loan_owner="business", loan_bank="Банк, уменьшить платёж",
            loan_balance=1_000_000, loan_payment=30_000, loan_months=36,
            startup_costs="Оборудование", contract_need="both", contract_details="Поставка, 1 млн, 60 дней",
            description="Требуется уточнение цели", owner_age=30, citizenship="rf", personal_history="none",
            season_details="Пик летом", revenue=0, revenue_period="май–июль 2026", free_cash=-2000)


def registry_fixture():
    return {"schema_version": "compact-1.0", "query": "4028051108", "status": "ok",
            "checked_at": "2026-10-24T10:00:00+00:00",
            "sources": {"egrul": {"status": "ok"}, "transparent_business": {"status": "ok"}, "girbo": {"status": "ok"}},
            "entities": [{"inn": "4028051108", "ogrn": "1124028002273", "entity_type": "legal",
                          "name": 'ООО "ТЕСТ"', "registered_on": "2012-04-20", "status": "Действующая организация",
                          "public_sources": {"girbo": {"status": "ok", "url": "https://bo.nalog.gov.ru/",
                                                       "checked_at": "2026-10-24T10:00:00+00:00",
                                                       "years": [{"year": 2025, "revenue_rub": 12000000, "assets_rub": 0}]}}}]}


class CreditApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.settings = ApiSettings(credit_db=Path(self.temp) / "credit.sqlite3", download_dir=Path(self.temp) / "extracts")
        self.app = create_app(self.settings)
        self.client = self.enterContext(TestClient(self.app))

    def create(self):
        response = self.client.post("/api/v1/credit/sessions")
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def answer(self, session, value, field=None):
        response = self.client.post(f"/api/v1/credit/sessions/{session['session_id']}/answers",
                                    json={"revision": session["revision"], "question_id": field or session["next_question"]["id"], "value": value})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def finish(self, values=None):
        values = {**BASE, **(values or {})}
        session = self.create()
        asked = []
        while session["next_question"]:
            question = session["next_question"]
            asked.append(question["id"])
            self.assertNotIn(question["question_id"], {"Q14", "Q15", "Q16", "Q38", "Q39", "Q40", "Q41"})
            self.assertIn(question["id"], values)
            session = self.answer(session, values[question["id"]])
            self.assertLess(len(asked), 80)
        return session, asked

    def attach(self, session, result=None, selected=None, identifier="4028051108", expected=200):
        result = result or registry_fixture()
        job = self.app.state.jobs.start(identifier, lambda handler: copy.deepcopy(result))
        for _ in range(100):
            if self.app.state.jobs.get(job["job_id"])["state"] == "completed":
                break
            time.sleep(.005)
        response = self.client.post(f"/api/v1/credit/sessions/{session['session_id']}/registry",
                                    json={"revision": session["revision"], "job_id": job["job_id"], "selected_ogrn": selected})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def test_question_contract_validation_and_version_conflicts(self):
        session = self.create()
        path = f"/api/v1/credit/sessions/{session['session_id']}"
        self.assertEqual(session["schema_version"], "credit-application-1.0")
        self.assertEqual(session["next_question"]["id"], "knowledge")
        self.assertIsNone(session["data"]["financials"]["revenue_monthly_rub"])
        self.assertIsNone(session["recommendation"])
        self.assertEqual(self.client.get(path + "/result").status_code, 409)
        for payload in ({"question_id": "amount", "value": 100}, {"question_id": "knowledge", "value": True},
                        {"question_id": "knowledge", "value": None}, {"question_id": "knowledge", "value": "bogus"}):
            response = self.client.post(path + "/answers", json={"revision": 0, **payload})
            self.assertIn(response.status_code, (409, 422))
            self.assertEqual(self.client.get(path).json()["revision"], 0)
        session = self.answer(session, "no")
        stale = self.client.post(path + "/answers", json={"revision": 0, "question_id": "goal", "value": "gap"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "revision_conflict")
        self.assertEqual(self.client.get(path).headers["cache-control"], "no-store")

    def test_all_goal_branches_and_combined_result(self):
        expected = {"gap": "P02", "stock": "P01", "investment": "P01", "property": "P08", "vehicle": "P09",
                    "refinance": "P07", "startup": "P10", "contract": "A02"}
        for goal in (*expected, "other"):
            with self.subTest(goal=goal):
                session, asked = self.finish({"goal": goal})
                self.assertEqual(session["state"], "ready")
                # Missing registry verification is an explicit Q13 clarification.
                self.assertEqual(session["recommendation"]["status"], "R02")
                session = self.attach(session)
                if goal in expected:
                    self.assertIn(expected[goal], [c["code"] for c in session["recommendation"]["candidates"]])
                else:
                    self.assertEqual(session["recommendation"]["status"], "R02")
                self.assertFalse(session["recommendation"]["is_bank_decision"])
                self.assertEqual(self.client.get(f"/api/v1/credit/sessions/{session['session_id']}/result").json(), session)

    def test_registry_wins_preserves_conflict_and_affects_recommendations(self):
        session, _ = self.finish({"form": "other_legal"})
        session = self.attach(session)
        self.assertEqual(session["data"]["borrower"]["form"], "ooo")
        self.assertEqual(session["answer_states"]["borrower.form"], "registry")
        self.assertEqual(session["conflicts"], [{"field": "borrower.form", "user_value": "other_legal", "registry_value": "ooo", "chosen_source": "fns_parser"}])
        self.assertEqual(session["recommendation"]["status"], "R01")
        result = registry_fixture()
        result["entities"][0]["terminated_on"] = "2026-01-01"
        result["entities"][0]["status"] = "Деятельность прекращена"
        session = self.attach(session, result)
        self.assertEqual(session["data"]["borrower"]["registered"], "no")
        self.assertEqual(session["recommendation"]["status"], "R03")
        self.assertEqual(session["recommendation"]["candidates"][0]["status"], "excluded")

    def test_annual_financials_do_not_replace_monthly_answers_or_unknowns(self):
        session, _ = self.finish({"seasonal": "yes"})
        session = self.attach(session)
        self.assertEqual(session["data"]["financials"]["revenue_monthly_rub"], 0)
        self.assertEqual(session["data"]["financials"]["free_cash_monthly_rub"], -2000)
        self.assertEqual(session["answer_states"]["financials.revenue_monthly_rub"], "provided")
        self.assertEqual(session["data"]["company"]["entities"][0]["public_sources"]["girbo"]["years"][0]["revenue_rub"], 12000000)
        other, asked = self.finish({"debt_payments": None, "arrears": None})
        self.assertNotIn("revenue", asked)
        self.assertIsNone(other["data"]["financials"]["revenue_monthly_rub"])
        self.assertNotIn("financials.revenue_monthly_rub", other["answer_states"])
        self.assertEqual(other["answer_states"]["financials.debt_payments_monthly_rub"], "unknown")
        self.assertIsNone(other["data"]["financials"]["arrears"])

    def test_repeated_limit_is_derived_and_branch_edit_drops_stale_answers(self):
        session, asked = self.finish({"goal": "stock", "regular": "yes"})
        self.assertNotIn("reuse", asked)
        self.assertEqual(session["data"]["financing"]["reuse"], "yes")
        self.assertEqual(session["answer_states"]["financing.reuse"], "derived")
        session = self.attach(session)
        unchanged = self.answer(session, "yes", "regular")
        self.assertEqual(unchanged["state"], "ready")
        changed = self.answer(unchanged, "vehicle", "goal")
        self.assertEqual(changed["next_question"]["id"], "vehicle")
        self.assertIsNone(changed["data"]["financing"]["reuse"])
        self.assertIsNone(changed["data"]["company"]["inn"])
        self.assertEqual(changed["data"]["company"]["entities"], [])
        self.assertIsNone(changed["recommendation"])

    def test_identity_validation_and_explicit_registration_selection(self):
        session, _ = self.finish({"form": "ip", "inn": "784806113663"})
        result = registry_fixture()
        entity = {"inn": "784806113663", "ogrn": "316784700262702", "entity_type": "individual", "terminated_on": "2021-01-01"}
        result["entities"] = [entity, {**entity, "ogrn": "326784700190738", "terminated_on": None, "status": "Действующий ИП"}]
        session = self.attach(session, result, identifier="784806113663")
        self.assertIsNone(session["data"]["company"]["selected_ogrn"])
        self.assertEqual(session["recommendation"]["status"], "R02")
        self.assertEqual(len(session["data"]["company"]["entities"]), 2)
        session = self.attach(session, result, selected="326784700190738", identifier="784806113663")
        self.assertEqual(session["recommendation"]["status"], "R01")
        self.attach(session, result, selected="1124028002273", identifier="784806113663", expected=422)
        self.attach(session, registry_fixture(), expected=409)
        # OGRN lookup also matches the declared INN, and selects the exact registration.
        legal, _ = self.finish()
        legal = self.attach(legal, identifier="1124028002273")
        self.assertEqual(legal["data"]["company"]["selected_ogrn"], "1124028002273")

    def test_source_failure_does_not_claim_registry_priority(self):
        session, _ = self.finish({"form": "other_legal"})
        result = registry_fixture()
        result["sources"] = {"egrul": {"status": "error"}, "transparent_business": {"status": "error"}}
        session = self.attach(session, result)
        self.assertEqual(session["data"]["borrower"]["form"], "other_legal")
        self.assertEqual(session["field_sources"], {})
        self.assertEqual(session["recommendation"]["status"], "R02")

    def test_five_removed_sources_not_returned_from_old_jobs(self):
        session, _ = self.finish()
        result = registry_fixture()
        removed = ("arbitration", "fssp", "fedresurs", "bankruptcy", "pledges")
        for name in removed:
            result["sources"][name] = {"status": "error"}
            result["entities"][0]["public_sources"][name] = {"status": "error"}
        result["status"] = "partial"
        session = self.attach(session, result)
        self.assertEqual(session["data"]["company"]["lookup_status"], "ok")
        for name in removed:
            self.assertNotIn(name, json.dumps(session))
        with patch("fns_parser.public_sources.FsspClient") as fssp, patch("fns_parser.public_sources.ArbitrationClient") as kad:
            self.assertEqual(set(default_clients(20, 3)), {"girbo"})
            fssp.assert_not_called()
            kad.assert_not_called()

    def test_sessions_survive_new_app_and_delete_has_revision_guard(self):
        session, _ = self.finish()
        session = self.attach(session)
        path = f"/api/v1/credit/sessions/{session['session_id']}"
        with TestClient(create_app(self.settings)) as other:
            self.assertEqual(other.get(path).json(), session)
            self.assertEqual(other.delete(path, params={"revision": 0}).status_code, 409)
            self.assertEqual(other.delete(path, params={"revision": session["revision"]}).status_code, 204)
            self.assertEqual(other.get(path).status_code, 404)

    def test_atomic_concurrent_answer_and_origin_guard(self):
        session = self.create()
        path = f"/api/v1/credit/sessions/{session['session_id']}/answers"
        payload = {"revision": 0, "question_id": "knowledge", "value": "no"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(path, json=payload).status_code, range(2)))
        self.assertEqual(sorted(responses), [200, 409])
        self.assertEqual(self.client.post("/api/v1/credit/sessions", headers={"Origin": "https://external.test"}).status_code, 403)

    def test_personal_products_and_ip_history_details(self):
        session, asked = self.finish({"registered": "no", "form": "self_employed", "goal": "refinance", "loan_owner": "personal"})
        self.assertNotIn("inn", asked)
        self.assertEqual(session["recommendation"]["candidates"][0]["code"], "P13")
        session, asked = self.finish({"form": "ip", "inn": "784806113663", "personal_history": "arrears", "personal_history_details": "Просрочка 2 дня"})
        self.assertIn("personal_history_details", asked)
        self.assertEqual(session["data"]["borrower"]["personal_history_details"], "Просрочка 2 дня")


class CreditLogicTests(unittest.TestCase):
    def test_calendar_month_and_subscription_boundaries(self):
        a = {**BASE, "today": date(2026, 10, 24), "needed_date": date(2026, 10, 24)}
        def codes(**values):
            return {c.code for c in recommend({**a, **values})["candidates"]}
        self.assertIn("P02", codes(gap_days=60))
        self.assertNotIn("P02", codes(gap_days=61))
        self.assertIn("P02", codes(gap_days=90, alpha_client="yes", subscription="yes", subscription_target="overdraft", subscription_valid="yes"))
        self.assertNotIn("P02", codes(gap_days=90, alpha_client="yes", subscription="yes", subscription_target="overdraft", subscription_valid="unknown"))
        r = recommend({**a, "gap_days": 182})  # Oct 24 to Apr 24 = 182 days, not 180.
        self.assertEqual(r["candidates"][0].status, "candidate")
        r = recommend({**a, "gap_days": 183})
        self.assertEqual(r["candidates"][0].status, "clarify")

    def test_invalid_values_do_not_mutate_record(self):
        with TemporaryDirectory() as tmp:
            record = SessionStore(Path(tmp) / "db.sqlite3").create()
            for key in ("knowledge", "goal", "gap_days", "receivable", "registered", "form"):
                submit(record, key, BASE[key])
            original = copy.deepcopy(record)
            for value in ("4028051109", "784806113663", "0000000000"):
                with self.assertRaises(CreditError):
                    submit(record, "inn", value)
                self.assertEqual(record, original)
            submit(record, "inn", BASE["inn"])
            submit(record, "alpha_client", "no")
            for value in (-1, True, "100", float("inf"), 10**400):
                with self.assertRaises(CreditError):
                    submit(record, "amount", value)
            submit(record, "amount", 1_000_000)
            for value in ("2026-02-30", "24.10.2026", "9999-12-01", 20261024):
                with self.assertRaises(CreditError):
                    submit(record, "needed_date", value)

    def test_registration_form_consistency(self):
        with TemporaryDirectory() as tmp:
            record = SessionStore(Path(tmp) / "db.sqlite3").create()
            for key in ("knowledge", "goal", "gap_days", "receivable", "registered"):
                submit(record, key, BASE[key])
            with self.assertRaises(CreditError):
                submit(record, "form", "none")


if __name__ == "__main__":
    unittest.main()
