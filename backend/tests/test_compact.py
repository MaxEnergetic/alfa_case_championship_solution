import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fns_parser import compact_result
from fns_parser.__main__ import main


def fixture():
    return {
        "schema_version": "1.0", "query": "4028051108", "status": "ok", "finished_at": "2026-10-20T12:00:00Z",
        "sources": {"egrul": {"status": "ok"}, "transparent_business": {"status": "ok", "search_responses": ["raw"]}},
        "entities": [{
            "inn": "4028051108", "ogrn": "1124028002273", "entity_type": "legal",
            "summary": {"short_name": "TEST", "registered_on": "20.04.2012", "main_okved": {"code": "99.99"}},
            "egrul": {"status": "ok", "extract": {"fields": [], "pages": ["large raw text"], "formed_on": "20.10.2026"}},
            "transparent_business": {"status": "ok", "data": {
                "token": "temporary-token", "unknown": "raw",
                "form1": [{"yearcode": 2024, "revenue": 100, "expense": 90},
                          {"yearcode": 2025, "revenue": 0, "expense": 0},
                          {"yearcode": 2023, "revenue": 0, "empty": 1}],
                "taxpay": [{"yearcode": "2024", "taxsum": "0.1"}, {"yearcode": 2024.0, "taxsum": 0.2},
                           {"yearcode": 2025, "taxsum": 0}],
                "sschr": [{"yearcode": 2024, "sschr": 10}, {"yearcode": 2025, "sschr": 0}],
                "arrear": [{"yearcode": 2026, "periodcode": 9, "empty": 1}],
                "taxmode": [{"yearcode": 2026, "periodcode": 8, "usn": 1},
                            {"yearcode": 2026, "periodcode": 9, "usn": 0, "ausn": 1}],
            }},
        }],
    }


class CompactTests(unittest.TestCase):
    def test_projection_preserves_zero_and_years_without_mutating_source(self):
        full = fixture()
        before = copy.deepcopy(full)
        result = compact_result(full)
        entity = result["entities"][0]
        self.assertEqual(entity["financials"], [
            {"year": 2025, "income": 0, "expenses": 0, "taxes_paid": 0},
            {"year": 2024, "income": 100, "expenses": 90, "taxes_paid": 0.3},
        ])
        self.assertEqual(entity["employees"], {"year": 2025, "count": 0})
        self.assertEqual(entity["tax_debt"], {"period": "2026-09", "status": "no_data"})
        self.assertEqual(entity["tax_regime"], {"period": "2026-09", "regimes": ["АУСН"]})
        self.assertEqual(entity["registered_on"], "2012-04-20")
        serialized = json.dumps(result, allow_nan=False)
        for raw in ("temporary-token", "large raw text", "search_responses", "unknown"):
            self.assertNotIn(raw, serialized)
        self.assertEqual(full, before)

    def test_latest_debt_sums_only_complete_components(self):
        full = fixture()
        rows = full["entities"][0]["transparent_business"]["data"]["arrear"] = [
            {"yearcode": 2025, "periodcode": 12, "totalsum": 999},
            {"yearcode": 2026, "periodcode": 9, "totalsum": 0.1},
            {"yearcode": 2026, "periodcode": 9, "totalsum": 0.2},
        ]
        self.assertEqual(compact_result(full)["entities"][0]["tax_debt"],
                         {"period": "2026-09", "status": "available", "amount": 0.3})
        rows[-1].pop("totalsum")
        self.assertEqual(compact_result(full)["entities"][0]["tax_debt"],
                         {"period": "2026-09", "status": "incomplete"})
        rows[-1]["totalsum"] = 0
        rows[-2]["totalsum"] = 0
        self.assertEqual(compact_result(full)["entities"][0]["tax_debt"]["amount"], 0)

    def test_pdf_ownership_and_closure_override_older_pb(self):
        full = fixture()
        entity = full["entities"][0]
        entity["summary"].update(terminated_on="25.01.2021", status_from_pb="Действующая организация")
        entity["transparent_business"]["data"]["vyp"] = {"masuchr": [{"name": "OLD OWNER"}]}
        fields = entity["egrul"]["extract"]["fields"]
        for item, name, share in [(1, "FIRST", "75,5"), (2, "SECOND", "24,5")]:
            for label, value in [("Фамилия Имя Отчество", name), ("Размер доли (в процентах)", share)]:
                fields.append({"section": "Сведения об участниках / учредителях юридического лица",
                               "item_number": item, "label": label, "value": value})
        fields.extend([
            {"section": "Сведения об основном виде деятельности", "label": "Код и наименование вида деятельности", "value": "62.01 Test"},
            {"section": "Сведения об уставном капитале", "label": "Размер (в рублях)", "value": "12 000"},
            {"section": "Сведения о записях, внесенных в ЕГРЮЛ", "label": "Фамилия Имя Отчество", "value": "HISTORY"},
        ])
        compact = compact_result(full)["entities"][0]
        self.assertEqual(compact["founders"], [{"name": "FIRST", "share_percent": 75.5}, {"name": "SECOND", "share_percent": 24.5}])
        self.assertEqual(compact["main_okved"], "62.01")
        self.assertEqual(compact["capital_rub"], 12000)
        self.assertEqual(compact["status"], "Деятельность прекращена")
        self.assertEqual(compact["terminated_on"], "2021-01-25")

    def test_partial_source_warnings_and_multiple_registrations_survive(self):
        full = fixture()
        full["status"] = "partial"
        error = {"status": "error", "error": {"code": "captcha_required", "message": "captcha"}}
        full["sources"]["transparent_business"] = error
        entity = full["entities"][0]
        entity["transparent_business"] = error
        entity["egrul"]["status"] = "partial"
        entity["egrul"]["extract"]["warnings"] = ["Check PDF"]
        second = copy.deepcopy(entity)
        second.update(ogrn="326784700190738", entity_type="individual")
        full["entities"].append(second)
        result = compact_result(full)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["entities"]), 2)
        self.assertEqual(result["sources"]["transparent_business"], error)
        self.assertEqual(result["entities"][0]["issues"][0]["warnings"], ["Check PDF"])
        self.assertNotIn("financials", result["entities"][0])
        self.assertNotIn("capital_rub", result["entities"][0])

    def test_not_found_and_invalid_schema(self):
        full = fixture()
        full.update(status="not_found", entities=[])
        self.assertEqual(compact_result(full)["entities"], [])
        for value in ([], {}, {"schema_version": "compact-1.0", "entities": []}):
            with self.assertRaises(ValueError):
                compact_result(value)

    def test_cli_converts_file_without_fetch_and_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp, patch("fns_parser.__main__.parse_company") as fetch:
            source, target = Path(tmp, "full.json"), Path(tmp, "compact.json")
            source.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(main(["--from-json", str(source), "-o", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["schema_version"], "compact-1.0")
            source.write_text("[]", encoding="utf-8")
            self.assertEqual(main(["--from-json", str(source), "-o", str(target)]), 1)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["error"]["code"], "invalid_json_input")
            fetch.assert_not_called()

    def test_cli_selects_compact_by_default_and_full_explicitly(self):
        with patch("fns_parser.__main__.parse_company", return_value={"status": "ok"}) as fetch:
            for flags, expected in (([], False), (["--full"], True)):
                with patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(main(["4028051108", *flags]), 0)
                self.assertEqual(fetch.call_args.kwargs["full"], expected)


if __name__ == "__main__":
    unittest.main()
