import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer  # noqa: E402


class AmountTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(importer.parse_amount("1 234,56"), 1234.56)
        self.assertEqual(importer.parse_amount("-1 234,56 ₽"), -1234.56)
        self.assertEqual(importer.parse_amount("1.234,56"), 1234.56)
        self.assertEqual(importer.parse_amount("1,234.56"), 1234.56)
        self.assertIsNone(importer.parse_amount("нет суммы"))


class CsvHeaderTest(unittest.TestCase):
    def test_semicolon_header(self):
        text = (
            "дата;сумма;описание;тип\n"
            "05.11.2026;-300,50;кофе;расход\n"
            "06.11.2026;50000;зарплата;доход\n"
        )
        items = importer.parse_statement(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["amount"], 300.5)
        self.assertEqual(items[0]["kind"], "expense")
        self.assertEqual(items[0]["note"], "кофе")
        self.assertEqual(items[0]["date"], "2026-11-05 00:00:00")
        self.assertEqual(items[1]["kind"], "income")

    def test_debit_credit_columns(self):
        text = (
            "date,description,debit,credit\n"
            "2026-11-05,coffee,300,\n"
            "2026-11-06,salary,,50000\n"
        )
        items = importer.parse_statement(text)
        self.assertEqual([item["kind"] for item in items], ["expense", "income"])
        self.assertEqual([item["amount"] for item in items], [300.0, 50000.0])


class CsvPositionalTest(unittest.TestCase):
    def test_simple_two_columns(self):
        text = "-1500, яндекс плюс\n300, кофе\n"
        items = importer.parse_statement(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["kind"], "expense")
        self.assertEqual(items[0]["amount"], 1500.0)
        self.assertEqual(items[1]["kind"], "income")


class DateTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(importer.normalize_date("05.11.2026"), "2026-11-05 00:00:00")
        self.assertEqual(importer.normalize_date("2026-11-05 14:30"), "2026-11-05 14:30:00")
        self.assertEqual(importer.normalize_date("2026-11-05T14:30:00"), "2026-11-05 14:30:00")
        self.assertIsNone(importer.normalize_date("не дата"))


if __name__ == "__main__":
    unittest.main()
