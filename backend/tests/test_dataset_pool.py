"""Offline checks for cohort identity, missing values and selection integrity."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fns_parser.dataset_pool import QUOTAS, financial_case, join_financials, msp_rows, select


class DatasetPoolTests(unittest.TestCase):
    def test_missing_financials_are_not_zero_income(self):
        self.assertEqual(financial_case(None), "financials_not_in_release")
        self.assertEqual(financial_case({"income_rub": None, "expenses_rub": 0}), "financials_not_in_release")
        self.assertEqual(financial_case({"income_rub": 0, "expenses_rub": 0}), "zero_income")
        self.assertEqual(financial_case({"income_rub": 0, "expenses_rub": 10}), "zero_income")
        self.assertEqual(financial_case({"income_rub": 20, "expenses_rub": 10}), "income_gt_expenses")
        self.assertEqual(financial_case({"income_rub": 10, "expenses_rub": 20}), "expenses_gt_income")
        self.assertEqual(financial_case({"income_rub": 10, "expenses_rub": 10}), "equal_positive_income_expenses")

    def test_msp_only_legal_entities_and_distinguishes_zero_from_missing(self):
        doc = '<Документ ДатаСост="10.10.2026" ДатаВклМСП="01.08.2016" {employee}><ОргВклМСП ИННЮЛ="4028051108" ОГРН="1124028002273" НаимОрг="Fixture"/><СвОКВЭДотч><СвОКВЭДОсн КодОКВЭД="47.11"/></СвОКВЭДотч></Документ>'
        xml = '<Файл>' + doc.format(employee='ССЧР="0"') + doc.format(employee='') + '<Документ><ИПВклМСП ИННФЛ="123"/></Документ></Файл>'
        rows, invalid = msp_rows(xml.encode(), "fixture.xml")
        self.assertEqual(invalid, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["employees"], 0)
        self.assertIsNone(rows[1]["employees"])
        self.assertEqual(rows[0]["msp_included_on"], "2016-08-01")
        self.assertNotIn("registration_date", rows[0])
        self.assertIsNone(rows[0]["main_okved"])
        self.assertEqual(rows[0]["reported_main_okved"], "47.11")
        self.assertEqual(rows[0]["industry_bucket"], "trade")
        self.assertEqual(rows[0]["industry_bucket_basis"], "reported_main_okved")

    def test_financial_join_excludes_ambiguity_and_preserves_period(self):
        def record(inn, income):
            return f'<Документ ДатаСост="31.12.2025" ДатаДок="25.09.2026"><СведНП ИННЮЛ="{inn}"/><СведДохРасх СумДоход="{income}" СумРасход="0.00"/></Документ>'
        pool = {key: {"inn": key} for key in ("one", "ambiguous", "missing", "negative")}
        xml = '<Файл>' + record("one", "10.25") + record("ambiguous", "10") + record("ambiguous", "20") + record("negative", "-10") + '</Файл>'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("fixture.xml", xml)
            metadata = join_financials(pool, path)
        self.assertEqual(metadata["conflicting_inns_excluded"], 1)
        self.assertNotIn("ambiguous", pool)
        self.assertNotIn("negative", pool)
        self.assertEqual(metadata["negative_amount_inns_excluded"], 1)
        self.assertEqual(pool["one"]["financials"]["income_rub"], 10.25)
        self.assertEqual(pool["one"]["financials"]["period_end"], "2025-12-31")
        self.assertNotIn("financials", pool["missing"])

    @staticmethod
    def candidates(extra=True):
        values = [(20, 10), (10, 20), (0, 0), (10, 10), (None, None)]
        pool = {}
        for (case, quota), (income, expense) in zip(QUOTAS.items(), values):
            for index in range(quota + (30 if extra else 0)):
                key = f"fixture-{case}-{index}"
                pool[key] = {"inn": key, "ogrn": "ogrn-" + key, "region_code": str(index % 12),
                             "industry_bucket": str(index % 7), "msp_category_code": str(index % 3),
                             "financials": None if income is None else {"income_rub": income, "expenses_rub": expense}}
        return pool

    def test_selection_unique_balanced_and_independent_of_input_order(self):
        pool = self.candidates()
        original = json.dumps(pool, sort_keys=True)
        rows, metadata = select(pool)
        reversed_rows, _ = select(dict(reversed(list(pool.items()))))
        self.assertEqual(rows, reversed_rows)
        self.assertEqual(len(rows), 10000)
        self.assertEqual(len({row["inn"] for row in rows}), 10000)
        self.assertEqual(Counter(financial_case(row["financials"]) for row in rows), QUOTAS)
        self.assertEqual(metadata["top_up_count"], 0)
        self.assertEqual(json.dumps(pool, sort_keys=True), original)

    def test_insufficient_case_is_reported_without_fabricating_records(self):
        pool = self.candidates()
        pool = {key: row for key, row in pool.items() if financial_case(row["financials"]) != "equal_positive_income_expenses"}
        with self.assertRaisesRegex(ValueError, "10,000"):
            select(pool)

    def test_top_up_preserves_truthful_actual_case_counts(self):
        pool = self.candidates()
        keys = [key for key, row in pool.items() if financial_case(row["financials"]) == "equal_positive_income_expenses"]
        for key in keys[:50]:
            del pool[key]
        rows, metadata = select(pool)
        self.assertEqual(len(rows), 10000)
        self.assertEqual(metadata["top_up_count"], 20)
        self.assertEqual(metadata["quotas_before_top_up"]["equal_positive_income_expenses"], 480)


if __name__ == "__main__":
    unittest.main()
