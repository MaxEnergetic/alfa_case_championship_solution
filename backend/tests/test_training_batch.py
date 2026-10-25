import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fns_parser.batch_training import collect, export_files, export_run, load_requests, main, read_json, run_lock
from fns_parser.training import (CATEGORICAL, EXTRA_COLUMNS, TEMPLATE_COLUMNS, atomic_json, build_schema,
                                 category_column, encode, export_tables, normalize_request, project, raw_row)

COMPANIES = [
    {"inn": "4028051108", "ogrn": "1124028002273"},
    {"inn": "0224009722", "ogrn": "1060224012155"},
    {"inn": "0224010260", "ogrn": "1070273004339"},
]


def fixture(company=COMPANIES[0], status="ok", error=None):
    result = {"schema_version": "compact-1.0", "query": company["ogrn"], "checked_at": "2026-10-24T15:00:00Z",
              "status": status, "sources": {"egrul": {"status": "ok"}, "transparent_business": {"status": "ok"},
                                           "girbo": {"status": "ok"}}, "entities": []}
    if status != "error":
        result["entities"] = [{**company, "name": "TEST", "entity_type": "legal", "status": "active",
                               "registered_on": "2012-04-20", "main_okved": "71.12", "capital_rub": 0,
                               "founders": [{"share_percent": 100}], "employees": {"year": 2025, "count": 0},
                               "financials": [{"year": 2025, "income": 0, "expenses": 0, "taxes_paid": 6600},
                                              {"year": 2023, "income": 86000, "expenses": 81000}],
                               "tax_regime": {"regimes": ["УСН", "ПСН"], "period": "2026-09"},
                               "tax_debt": {"status": "no_data", "period": "2026-09"},
                               "public_sources": {"girbo": {"years": [{"year": 2025, "revenue_rub": 999}], "status": "ok"}}}]
    if error:
        result["sources"]["transparent_business"] = {"status": "error", "error": {"code": error, "message": "test"}}
    return result


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


class TrainingProjectionTests(unittest.TestCase):
    def test_real_zero_missing_and_unlabelled_are_distinct(self):
        source = fixture()
        before = copy.deepcopy(source)
        row, origin = project(source, COMPANIES[0], "sample.json")
        flat = raw_row(row, TEMPLATE_COLUMNS + EXTRA_COLUMNS)
        self.assertEqual((flat["income_t0_raw"], flat["income_t0_raw_missing"]), (0, 0))
        self.assertEqual((flat["employees_count"], flat["employees_count_missing"]), (0, 0))
        self.assertEqual((flat["tax_debt_amount_rub"], flat["tax_debt_amount_rub_missing"]), (0, 1))
        self.assertEqual((flat["target_default_12m"], flat["target_default_12m_missing"]), (0, 1))
        self.assertEqual(flat["meta_label_status"], "no_loan_history")
        self.assertIsNone(row["monthly_operating_inflows_avg_6m_rub"])
        self.assertIsNone(row["requested_product_code"])
        self.assertEqual(source, before)
        self.assertIn("income_t0_raw", origin["field_sources"])

    def test_year_gap_does_not_shift_prior_data(self):
        row, _ = project(fixture(), COMPANIES[0])
        self.assertEqual([row[f"financial_year_t{i}"] for i in range(3)], [2025, 2024, 2023])
        self.assertIsNone(row["income_t1_raw"])
        self.assertEqual(row["income_t2_raw"], 86000)
        self.assertEqual(row["income_t0_raw"], 0)  # GIR BO revenue must not replace PB income.

    def test_missing_source_and_nonfinite_values(self):
        doc = fixture()
        entity = doc["entities"][0]
        entity.pop("financials")
        entity["capital_rub"] = float("nan")
        entity["founders"].append({"name": "second"})
        row, origin = project(doc, COMPANIES[0])
        self.assertIsNone(row["capital_rub"])
        self.assertIn("capital_rub", origin["invalid_or_unavailable_values"])
        self.assertIsNone(row["largest_founder_share_pct"])
        self.assertIsNone(row["financial_year_t0"])

    def test_identity_mismatch_and_ambiguous_inn_fail(self):
        with self.assertRaises(ValueError):
            project(fixture(), COMPANIES[1])
        doc = fixture()
        extra = copy.deepcopy(doc["entities"][0])
        extra["ogrn"] = COMPANIES[1]["ogrn"]
        doc["entities"].append(extra)
        with self.assertRaisesRegex(ValueError, "Несколько"):
            project(doc, COMPANIES[0]["inn"])

    def test_one_hot_unseen_missing_multi_value_and_leakage_exclusion(self):
        row, _ = project(fixture(), COMPANIES[0])
        schema = build_schema([row], TEMPLATE_COLUMNS + EXTRA_COLUMNS)
        encoded = encode(row, schema)
        self.assertEqual(encoded[category_column("entity_type", "legal")], 1)
        self.assertEqual(encoded[category_column("tax_regime", "УСН")], 1)
        self.assertEqual(encoded[category_column("tax_regime", "ПСН")], 1)
        self.assertEqual(encoded["purpose_missing"], 1)
        self.assertEqual(encoded["purpose__unseen"], 0)
        unseen = {**row, "entity_type": "individual", "tax_regime": None}
        other = encode(unseen, schema)
        self.assertEqual(other["entity_type__unseen"], 1)
        self.assertEqual(other["entity_type_missing"], 0)
        self.assertEqual(other["tax_regime_missing"], 1)
        self.assertTrue(all(not name.startswith(("meta_", "outcome_", "target_")) for name in schema["feature_columns"]))
        self.assertTrue(all(type(encoded[key]) in (int, float) for key in schema["feature_columns"]))
        self.assertGreater(encoded["registered_on"], 0)

    def test_export_template_columns_stable_schema_and_no_empty_cells(self):
        row, origin = project(fixture(), COMPANIES[0])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            export_tables([row], [origin], root / "train")
            altered = {**row, "registry_status": "new status", "meta_inn": "0224009722"}
            export_tables([altered], [origin], root / "test", encoder_schema=root / "train/encoder.json")
            train = csv_rows(root / "train/dataset.csv")[0]
            test = csv_rows(root / "test/dataset.csv")[0]
            self.assertEqual(list(train), list(test))
            self.assertEqual(test["registry_status__unseen"], "1")
            self.assertEqual(test["meta_inn"], "0224009722")
            raw = csv_rows(root / "train/dataset_raw.csv")[0]
            self.assertEqual(list(raw)[:65], TEMPLATE_COLUMNS)
            self.assertEqual(raw["tax_regime"], "ПСН|УСН")
            self.assertNotIn("", raw.values())
            self.assertNotIn("", test.values())
            self.assertTrue(all(float(value) == float(value) for value in csv_rows(root / "train/features.csv")[0].values()))

    def test_offline_full_and_compact_json(self):
        from tests.test_compact import fixture as full_fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            atomic_json(root / "full.json", full_fixture())
            result = export_files([root / "full.json"], root / "out")
            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["labelled_rows"], 0)
            self.assertEqual(csv_rows(root / "out/dataset_raw.csv")[0]["income_t0_raw_missing"], "0")


class BatchCollectionTests(unittest.TestCase):
    def test_fast_mode_passes_previous_attempt_but_refresh_begins_fresh(self):
        requests = [normalize_request(COMPANIES[0])]
        partial = fixture(status='partial', error='network_error')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fetch = Mock(side_effect=[partial, fixture()])
            collect(requests, root, fast=True, parser_func=fetch, sleep=lambda _: None)
            self.assertTrue(fetch.call_args_list[0].kwargs['parallel_sources'])
            self.assertIsNone(fetch.call_args_list[0].kwargs['previous_result'])
            self.assertEqual(fetch.call_args_list[1].kwargs['previous_result'], partial)
            refresh = Mock(return_value=fixture())
            collect(requests, root, fast=True, refresh=True, parser_func=refresh)
            self.assertIsNone(refresh.call_args.kwargs['previous_result'])

    def test_input_validation_deduplication_and_leading_zeroes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            atomic_json(path, [COMPANIES[1], COMPANIES[1]])
            requests = load_requests(path)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["inn"], "0224009722")
            atomic_json(path, [{"inn": 224009722}])
            with self.assertRaises(ValueError):
                load_requests(path)

    def test_resume_skips_completed_and_retry_only_failed(self):
        requests = [normalize_request(company) for company in COMPANIES[:2]]
        fetch = Mock(side_effect=[fixture(), fixture(COMPANIES[1], "error", "network_error")])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            collect(requests, output, attempts=1, delay=0, parser_func=fetch, sleep=lambda _: None)
            report = export_run(output)
            self.assertEqual(report["rows"], 2)
            self.assertEqual(report["last_attempt_statuses"], {"ok": 1, "error": 1})
            self.assertEqual(csv_rows(output / "dataset_raw.csv")[1]["capital_rub_missing"], "1")
            cached = Mock()
            run = collect(requests, output, parser_func=cached)
            cached.assert_not_called()
            self.assertEqual(run["cached_companies"], 2)
            retry = Mock(return_value=fixture(COMPANIES[1]))
            collect(requests, output, retry_failed=True, parser_func=retry)
            retry.assert_called_once()
            self.assertEqual(retry.call_args.args[0], COMPANIES[1]["ogrn"])
            self.assertTrue(retry.call_args.kwargs["full"])
            self.assertTrue(retry.call_args.kwargs["enrich"])
            self.assertEqual(export_run(output)["last_attempt_statuses"], {"ok": 2})
            self.assertEqual(len(list((output / "raw").glob("*.json"))), 3)

    def test_captcha_stops_before_next_company_and_preserves_partial(self):
        requests = [normalize_request(company) for company in COMPANIES]
        fetch = Mock(return_value=fixture(status="partial", error="captcha_required"))
        with tempfile.TemporaryDirectory() as temp:
            run = collect(requests, Path(temp), parser_func=fetch)
            fetch.assert_called_once()
            self.assertEqual(run["stopped_on"]["code"], "captcha_required")
            report = export_run(Path(temp))
            self.assertEqual(report["last_attempt_statuses"], {"partial": 1, "not_processed": 2})
            self.assertEqual(report["rows"], 3)

    def test_keyboard_interrupt_then_resume(self):
        requests = [normalize_request(company) for company in COMPANIES[:2]]
        fetch = Mock(side_effect=[fixture(), KeyboardInterrupt()])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = collect(requests, root, parser_func=fetch, sleep=lambda _: None)
            self.assertTrue(run["interrupted"])
            self.assertEqual(export_run(root)["last_attempt_statuses"], {"ok": 1, "not_processed": 1})
            retry = Mock(return_value=fixture(COMPANIES[1]))
            collect(requests, root, parser_func=retry)
            retry.assert_called_once()

    def test_failed_refresh_keeps_previous_success_visible_as_old_attempt(self):
        requests = [normalize_request(COMPANIES[0])]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collect(requests, root, parser_func=Mock(return_value=fixture()))
            collect(requests, root, attempts=1, refresh=True,
                    parser_func=Mock(return_value=fixture(status="error", error="network_error")))
            report = export_run(root)
            self.assertEqual(report["selected_statuses"], {"ok": 1})
            self.assertEqual(report["last_attempt_statuses"], {"error": 1})
            row = csv_rows(root / "dataset_raw.csv")[0]
            self.assertEqual(row["meta_parser_status"], "ok")
            self.assertEqual(row["meta_last_attempt_status"], "error")
            self.assertEqual(row["capital_rub_missing"], "0")

    def test_cohort_cannot_silently_change_on_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collect([normalize_request(COMPANIES[0])], root, parser_func=Mock(return_value=fixture()))
            with self.assertRaisesRegex(ValueError, "Состав"):
                collect([normalize_request(COMPANIES[1])], root, parser_func=Mock())

    def test_directory_lock_rejects_second_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            with run_lock(Path(temp)):
                with self.assertRaises(ValueError):
                    with run_lock(Path(temp)):
                        self.fail("Lock should reject concurrent writer")

    def test_cli_export_and_invalid_template_make_no_network_calls(self):
        with tempfile.TemporaryDirectory() as temp, patch("fns_parser.batch_training.parse_company") as fetch:
            root = Path(temp)
            atomic_json(root / "source.json", fixture())
            with patch("sys.stdout", new=io.StringIO()):
                code = main(["export", "--json", str(root / "source.json"), "--output", str(root / "out")])
            self.assertEqual(code, 0)
            (root / "bad.csv").write_text("wrong\n", encoding="utf-8")
            with patch("sys.stderr", new=io.StringIO()):
                code = main(["run", "--template", str(root / "bad.csv"), "--output", str(root / "out")])
            self.assertEqual(code, 1)
            fetch.assert_not_called()

    def test_invalid_encoder_is_rejected_before_collection(self):
        with tempfile.TemporaryDirectory() as temp, patch("fns_parser.batch_training.parse_company") as fetch:
            root = Path(temp)
            for invalid in ({"schema_version": "wrong"}, [], None):
                atomic_json(root / "encoder.json", invalid)
                with patch("sys.stderr", new=io.StringIO()):
                    code = main(["run", "--encoder-schema", str(root / "encoder.json"), "--output", str(root / "out")])
                self.assertEqual(code, 1)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
