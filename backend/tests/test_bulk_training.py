import copy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from fns_parser.bulk_sources import parse_archive, project_document
from fns_parser.bulk_training import combine, build
from fns_parser.training import raw_row, TEMPLATE_COLUMNS, EXTRA_COLUMNS, atomic_json
from tests.test_training_batch import fixture, COMPANIES, csv_rows


def company():
    return {**COMPANIES[0], 'name': 'TEST', 'main_okved': '71.12',
            'employees': 0, 'employees_year': 2025, 'msp_as_of': '2026-10-10',
            'msp_source_member': 'member.xml', 'msp_source_record': 'msp-record',
            'financials': {'period_end': '2025-12-31', 'document_date': '2026-09-25',
                           'income_rub': 0, 'expenses_rub': 10, 'source_member': 'fin.xml',
                           'source_record': 'fin-record'}}


class BulkSourcesTests(unittest.TestCase):
    def doc(self, contents):
        return ET.fromstring('<Документ ИдДок="id" ДатаДок="25.09.2026" ДатаСост="01.09.2026">'
                             '<СведНП ИННЮЛ="4028051108"/>'+contents+'</Документ>')

    def test_regimes_and_money_are_read_from_exact_fields(self):
        doc = self.doc('<СведСНР ПризнУСН="1" ПризнЕСХН="0" ПризнАУСН="0" ПризнСРП="1"/>')
        self.assertEqual(project_document(doc, 'snr', 'x')['regimes'], ['СРП', 'УСН'])
        doc = self.doc('<СвУплСумНал СумУплНал="10.10"/><СвУплСумНал СумУплНал="0.20"/>')
        result = project_document(doc, 'paytax', 'x')
        self.assertEqual(result['taxes_paid_rub'], 10.3)
        self.assertEqual(result['year'], 2025)
        doc = self.doc('<СведНедоим СумНедНалог="10" СумПени="1" СумШтраф="2" ОбщСумНедоим="13"/>')
        self.assertEqual(project_document(doc, 'debtam', 'x')['amount_rub'], 13)  # Do not sum the total twice.

    def test_invalid_flags_and_missing_amounts_fail(self):
        with self.assertRaises(ValueError):
            project_document(self.doc('<СведСНР ПризнУСН="2"/>'), 'snr', 'x')
        with self.assertRaises(ValueError):
            project_document(self.doc(''), 'paytax', 'x')

    def test_archive_does_not_invent_absent_inns_or_accept_conflicts(self):
        doc = self.doc('<СведНедоим ОбщСумНедоим="0"/>')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'data.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('one.xml', '<Файл>'+ET.tostring(doc, encoding='unicode')+'</Файл>')
            found, report = parse_archive('debtam', path, {'4028051108', '0224009722'})
            self.assertEqual(set(found), {'4028051108'})
            self.assertEqual(found['4028051108']['amount_rub'], 0)
            self.assertEqual(report['errors'], [])
            doc.find('СведНедоим').set('ОбщСумНедоим', '5')
            with zipfile.ZipFile(path, 'a') as z:
                z.writestr('two.xml', '<Файл>'+ET.tostring(doc, encoding='unicode')+'</Файл>')
            found, report = parse_archive('debtam', path, {'4028051108'})
            self.assertEqual(found, {})
            self.assertEqual(len(report['errors']), 1)


class BulkTrainingTests(unittest.TestCase):
    def test_bulk_zero_absence_and_labels_remain_distinct(self):
        doc, row, trace = combine(company(), {})
        encoded = raw_row(row, TEMPLATE_COLUMNS+EXTRA_COLUMNS)
        self.assertEqual((encoded['income_t0_raw'], encoded['income_t0_raw_missing']), (0, 0))
        self.assertEqual((encoded['tax_debt_amount_rub'], encoded['tax_debt_amount_rub_missing']), (0, 1))
        self.assertEqual((encoded['target_default_12m'], encoded['target_default_12m_missing']), (0, 1))
        self.assertEqual(encoded['tax_regime_missing'], 1)
        self.assertIsNone(row['registered_on'])  # MSP inclusion date is not incorporation.
        self.assertEqual(doc['status'], 'bulk_only')
        self.assertEqual(trace['field_sources']['income_t0_raw']['source'], 'income_expenses')

    def test_parser_fills_only_correct_years_and_preserves_bulk_values(self):
        parsed = fixture()
        parsed['entities'][0]['financials'] = [{'year': 2024, 'income': 80, 'expenses': 70, 'taxes_paid': 5},
                                              {'year': 2023, 'income': 60, 'expenses': 50}]
        before = copy.deepcopy(parsed)
        supplemental = {'paytax': {'companies': {COMPANIES[0]['inn']: {'year': 2025, 'taxes_paid_rub': 0,
                         'as_of': '2025-12-31', 'published_on': '2026-04-01', 'source_url': 'official'}}}}
        _, row, trace = combine(company(), supplemental, parsed)
        self.assertEqual([row[f'income_t{i}_raw'] for i in range(3)], [0, 80, 60])
        self.assertEqual([row[f'taxes_paid_t{i}_raw'] for i in range(3)], [0, 5, None])
        self.assertEqual(row['registered_on'], '2012-04-20')
        self.assertEqual(row['founders_count'], 1)
        self.assertEqual(trace['field_sources']['taxes_paid_t0_raw']['source'], 'paytax')
        self.assertEqual(parsed, before)

    def test_mismatched_parser_identity_fails(self):
        with self.assertRaises(ValueError):
            combine(company(), {}, fixture(COMPANIES[1]))

    def test_complete_export_has_real_rows_and_missing_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pool = root/'companies.jsonl'
            pool.write_text(json.dumps(company())+'\n', encoding='utf-8')
            atomic_json(root/'identifiers.json', [COMPANIES[0]])
            report = build(pool, root/'cache', root/'parser', root/'out')
            self.assertEqual(report['rows'], 1)
            self.assertEqual(report['statuses'], {'bulk_only': 1})
            row = csv_rows(root/'out/dataset_raw.csv')[0]
            self.assertEqual(row['employees_count_missing'], '0')
            self.assertEqual(row['registered_on_missing'], '1')
            self.assertEqual(row['meta_last_attempt_status'], 'not_processed')
            self.assertEqual(list(row)[:65], TEMPLATE_COLUMNS)
            self.assertEqual(report['labelled_rows'], 0)


if __name__ == '__main__':
    unittest.main()
