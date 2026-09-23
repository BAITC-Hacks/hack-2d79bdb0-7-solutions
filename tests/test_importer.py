"""All workbooks and business values in these tests are synthetic."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from importer import import_zip, merge_datasets


def synthetic_zip(supplier='IEK', quantity=8):
    files = {
        'Ежемесячные продажи.xlsx': [
            ['Наименование', 'Код', 'Артикул', 'Кратность', 'Авг 2026', 'Сен 2026'],
            ['PRIVATE PRODUCT', '000_1', 'SKU_01', 1, quantity, 0],
            ['No dynamics', '000_2', 'SKU_02', 1, 0, None]],
        'Динамика.xlsx': [
            ['Дата', 'Документ', 'Тип', 'Код', '', '', 'Склад', 'Количество'],
            ['01.08.2026 00:00:00', 'PRIVATE DOC', 'Расходная накладная', '000_1', '', '', 'PRIVATE WH', 10],
            [datetime(2026, 8, 2), 'PRIVATE DOC2', 'Расходная накладная', '000_1', '', '', 'PRIVATE WH', -1],
            ['03.08.2026 00:00:00', 'PRIVATE DOC3', 'Корректировка', '000_1', '', '', '', -2],
            ['04.08.2026 00:00:00', 'PRIVATE DOC4', 'Другой документ', '000_1', '', '', '', 1],
            ['01.09.2026 00:00:00', 'PRIVATE DOC5', 'Расходная накладная', '000_1', '', '', '', 0],
            ['01.07.2026 00:00:00', 'PRIVATE DOC6', 'Расходная накладная', 'orphan_0', '', '', '', 5],
            ['invalid', 'PRIVATE DOC7', 'Расходная накладная', '000_1', '', '', '', 50],
            ['01.08.2026 00:00:00', 'PRIVATE DOC8', 'Расходная накладная', '000_1', '', '', '', 'invalid']]
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        for name, rows in files.items():
            wb = Workbook()
            for row in rows:
                wb.active.append(row)
            stream = io.BytesIO()
            wb.save(stream)
            wb.close()
            archive.writestr(supplier + '/' + name, stream.getvalue())
    return payload.getvalue()


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.ds = import_zip(synthetic_zip())
        self.report = self.ds['meta']['reconciliation']['IEK']
        self.rows = {(r['code'], r['month']): r for r in self.report['rows']}

    def test_signed_quantities_and_other_documents_are_separate(self):
        row = self.rows['000_1', '2026-08']
        self.assertEqual([row[k] for k in ('invoice_positive', 'invoice_negative',
            'other_positive', 'other_negative', 'dynamics_net', 'delta')], [10, -1, 1, -2, 8, 0])
        self.assertEqual(row['status'], 'matched')
        self.assertEqual(self.ds['meta']['transactions'], 2)  # includes orphan positive invoice
        self.assertEqual(self.report['skipped_rows'], 2)

    def test_missing_dynamics_is_not_zero_and_blank_monthly_is_zero(self):
        row = self.rows['000_2', '2026-09']
        self.assertEqual(row['monthly_sales'], 0)
        self.assertIsNone(row['dynamics_net'])
        self.assertIsNone(row['delta'])
        self.assertEqual(row['status'], 'missing_dynamics')

    def test_zero_transaction_and_current_month(self):
        row = self.rows['000_1', '2026-09']
        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['dynamics_net'], 0)
        self.assertTrue(row['partial_month'])
        self.assertFalse(self.rows['000_1', '2026-08']['partial_month'])

    def test_orphan_code_retained_only_in_diagnostics(self):
        row = self.rows['orphan_0', '2026-07']
        self.assertEqual(row['status'], 'missing_monthly')
        self.assertIsNone(row['monthly_sales'])
        self.assertNotIn('orphan_0', [i['code'] for i in self.ds['items']])

    def test_difference_direction_and_tolerance(self):
        for sales, expected in [(9, 'difference'), (8.0000001, 'matched')]:
            ds = import_zip(synthetic_zip(quantity=sales))
            row = ds['meta']['reconciliation']['IEK']['rows'][0]
            self.assertEqual(row['status'], expected)
            self.assertAlmostEqual(row['delta'], 8-sales)

    def test_report_does_not_contain_document_warehouse_or_product_names(self):
        serialized = json.dumps(self.report)
        self.assertNotIn('PRIVATE', serialized)
        self.assertNotIn('document', self.rows['000_1', '2026-08'])

    def test_supplier_isolation_and_refresh(self):
        systeme = import_zip(synthetic_zip('Systeme Electric', quantity=15))
        merged = merge_datasets([self.ds, systeme])
        reports = merged['meta']['reconciliation']
        self.assertEqual(reports['IEK']['rows'][0]['delta'], 0)
        self.assertEqual(reports['Systeme Electric']['rows'][0]['delta'], -7)
        self.assertEqual(reports['Systeme Electric']['rows'][0]['sku'], 'SKU_01')
        refreshed = merge_datasets([merged, import_zip(synthetic_zip(quantity=10))])
        self.assertEqual(refreshed['meta']['reconciliation']['IEK']['rows'][0]['delta'], -2)
        self.assertEqual(refreshed['meta']['reconciliation']['Systeme Electric'], reports['Systeme Electric'])

    def test_cli_writes_separate_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output, report = (root / name for name in ('synthetic.zip', 'dataset.json', 'report.json'))
            archive.write_bytes(synthetic_zip())
            result = subprocess.run([sys.executable, 'importer.py', str(archive), '--output', str(output),
                                     '--reconciliation-output', str(report)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(report.read_text(encoding='utf-8')),
                             json.loads(output.read_text(encoding='utf-8'))['meta']['reconciliation'])


if __name__ == '__main__':
    unittest.main()
