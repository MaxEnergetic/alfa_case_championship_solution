import copy
import csv
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from fns_parser.bankruptcy_sources import free_pdf_link, PUBLISHER
from fns_parser.bankruptcy_training import (
    parse_notice, valid_report, historical_row, build, ADDITIONAL_COLUMNS, RAS_COLUMNS)
from fns_parser.training import export_tables, project, raw_row, TEMPLATE_COLUMNS, EXTRA_COLUMNS
from tests.test_training_batch import COMPANIES, fixture, csv_rows

SOURCE = {'url': 'https://example.test/notice.pdf', 'published_on': '2015-03-28', 'sha256': 'abc'}
NOTICE = ('Решением Арбитражного суда от 15.01.2015 по делу № А40-123/2014 '
          'ООО «ТЕСТ» (ИНН 4028051108, ОГРН 1124028002273) признано несостоятельным (банкротом). '
          'Конкурсным управляющим назначен Иванов И.И. (ИНН 123456789012).')


def company():
    return parse_notice(NOTICE, notice_id='12345678901', page=2, source=SOURCE)


def financial():
    return {**COMPANIES[0], 'creation_date': '2012-04-20', 'okved': '71.12',
            'filed': 1, 'imputed': 0, 'totals_adjustment': 0, 'financial': 0, 'outlier': 0,
            'line_2110': 0, 'line_2400': -20, 'line_1600': 300, 'line_1300': -10,
            'line_1250': None, 'line_1520': 20.5}


def metadata():
    return {2013: {'url': 'https://example.test/2013.parquet', 'sha256': 'abc'}}


class BankruptcyNoticesTests(unittest.TestCase):
    def test_matches_debtor_identifiers_and_decision(self):
        result = company()
        self.assertEqual(result['inn'], COMPANIES[0]['inn'])
        self.assertEqual(result['name'], 'ООО «ТЕСТ»')
        self.assertEqual(result['event_date'], '2015-01-15')
        self.assertEqual(result['case_number'], 'А40-123/2014')

    def test_operative_part_precedes_full_decision_and_russian_months(self):
        text = NOTICE.replace('15.01.2015', '15 января 2015 (резолютивная часть от 12.01.2015)')
        result = parse_notice(text, notice_id='1', page=1, source=SOURCE)
        self.assertEqual(result['event_date'], '2015-01-12')

    def test_non_event_ambiguous_cancelled_and_bad_identifiers_are_rejected(self):
        for text in [
            NOTICE.replace('Решением', 'Определением'),
            NOTICE.replace('признано несостоятельным (банкротом)', 'введено наблюдение'),
            NOTICE.replace('признано', 'в признании отказано, ранее признано'),
            NOTICE.replace('4028051108', '4028051109'),
            NOTICE.replace('1124028002273', '1124028002274'),
            NOTICE.replace('15.01.2015', '15.01.2015 (раньше 12.11.2013)'),
            NOTICE.replace('15.01.2015', '15.01.2016'),
            NOTICE.replace('ООО «ТЕСТ»', 'гражданин Иванов ИНН 123456789012, СРО ООО «ТЕСТ»'),
        ]:
            with self.subTest(text=text):
                self.assertIsNone(parse_notice(text, notice_id='1', page=1, source=SOURCE))

    def test_free_download_requires_explicit_link(self):
        html = '<span>Бесплатно</span><a class="download_pdf" href="/files/issue.pdf">PDF</a>'
        self.assertEqual(free_pdf_link(html, PUBLISHER), PUBLISHER + '/files/issue.pdf')
        self.assertIsNone(free_pdf_link(html.replace('Бесплатно', '263 руб.'), PUBLISHER))
        self.assertIsNone(free_pdf_link('<span>Бесплатно</span>', PUBLISHER))


class HistoricalFinancialTests(unittest.TestCase):
    def test_quality_and_identity_filters(self):
        self.assertTrue(valid_report(financial(), company(), 2013, '2014-12-31'))
        for key, value in [('filed', 0), ('imputed', 1), ('totals_adjustment', 1), ('financial', 1),
                           ('outlier', 1), ('inn', COMPANIES[1]['inn']), ('ogrn', COMPANIES[1]['ogrn'])]:
            with self.subTest(key=key):
                self.assertFalse(valid_report({**financial(), key: value}, company(), 2013, '2014-12-31'))
        self.assertFalse(valid_report(financial(), company(), 2014, '2014-12-31'))
        self.assertFalse(valid_report(financial(), company(), 2015, '2014-12-31'))

    def test_history_preserves_zero_and_unknown_and_does_not_forge_targets(self):
        row, trace = historical_row(company(), {2013: financial()}, metadata())
        self.assertEqual(row['ras_revenue_t0_rub'], 0)
        self.assertEqual(row['ras_net_profit_t0_rub'], -20000)
        self.assertEqual(row['ras_payables_t0_rub'], 20500)
        self.assertIsNone(row['ras_cash_t0_rub'])
        self.assertIsNone(row['income_t0_raw'])  # Revenue is not aggregate FNS income.
        self.assertIsNone(row['target_default_12m'])
        self.assertIsNone(row['registry_status'])
        self.assertEqual(row['target_bankruptcy_12m'], 1)
        self.assertEqual(row['meta_features_as_of'], '2014-12-31')
        self.assertEqual(row['meta_point_in_time_verified'], 0)
        self.assertEqual(row['meta_bankruptcy_training_eligible'], 0)
        self.assertIsNone(trace['field_sources']['ras_revenue_t0_rub']['available_on'])
        raw = raw_row(row, TEMPLATE_COLUMNS + EXTRA_COLUMNS + ADDITIONAL_COLUMNS)
        self.assertEqual((raw['ras_revenue_t0_rub'], raw['ras_revenue_t0_rub_missing']), (0, 0))
        self.assertEqual((raw['ras_cash_t0_rub'], raw['ras_cash_t0_rub_missing']), (0, 1))

    def test_future_or_imputed_history_cannot_enter_build(self):
        for history in ({2014: financial()}, {2013: {**financial(), 'imputed': 1}}):
            with self.assertRaises(ValueError):
                historical_row(company(), history, metadata())

    def test_combined_dataset_preserves_base_and_excludes_targets_from_features(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'base'
            out = root / 'combined'
            base_row, base_trace = project(fixture(COMPANIES[1]), COMPANIES[1])
            export_tables([base_row], [base_trace], base)
            report = build([company()], {COMPANIES[0]['inn']: {2013: financial()}}, metadata(), out, base, 1)
            raw = csv_rows(out / 'dataset_raw.csv')
            original = csv_rows(base / 'dataset_raw.csv')[0]
            self.assertEqual({k: raw[0][k] for k in original}, original)
            self.assertEqual(report['rows'], 2)
            self.assertEqual(report['conversion_errors'], [])
            self.assertEqual(raw[0]['target_bankruptcy_12m_missing'], '1')
            self.assertEqual(raw[1]['target_bankruptcy_12m_missing'], '0')
            self.assertEqual(raw[1]['target_default_12m_missing'], '1')
            features = csv_rows(out / 'features.csv')[0]
            self.assertFalse(any(c.startswith(('target_', 'outcome_', 'meta_')) for c in features))
            self.assertIn('entity_type__value_legal', features)
            self.assertTrue(all(c in features for c in RAS_COLUMNS))
            self.assertEqual(len(json.loads((out / 'identifiers.json').read_text())), 2)
            self.assertEqual(len(list((out / 'bankruptcies/records').glob('*.json'))), 1)
            self.assertFalse(json.loads((out / 'validation.json').read_text())['strict_point_in_time_training_ready'])

    def test_not_enough_real_data_raises_instead_of_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            row, trace = project(fixture(COMPANIES[1]), COMPANIES[1])
            export_tables([row], [trace], root / 'base')
            with self.assertRaisesRegex(ValueError, 'Only 0'):
                build([company()], {}, metadata(), root / 'out', root / 'base', 1)


if __name__ == '__main__':
    unittest.main()
