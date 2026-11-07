import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import categorizers  # noqa: E402


class ParseSingleTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(categorizers.parse_expense_message("300 продукты"), (300.0, "продукты"))
        self.assertEqual(categorizers.parse_expense_message("такси 450"), (450.0, "такси"))

    def test_decimal_comma(self):
        self.assertEqual(categorizers.parse_expense_message("кофе 1,5"), (1.5, "кофе"))

    def test_thousands_space(self):
        self.assertEqual(categorizers.parse_expense_message("такси 1 500"), (1500.0, "такси"))
        self.assertEqual(
            categorizers.parse_expense_message("продукты 1 500,50"),
            (1500.5, "продукты"),
        )

    def test_dot_thousands(self):
        self.assertEqual(
            categorizers.parse_expense_message("1.500 такси"), (1500.0, "такси")
        )
        self.assertEqual(categorizers.parse_expense_message("кофе 1.5"), (1.5, "кофе"))

    def test_date_is_not_amount(self):
        self.assertEqual(
            categorizers.parse_expense_message("05.11 продукты 300"),
            (300.0, "продукты"),
        )
        self.assertEqual(
            categorizers.parse_expense_message("2026-11-05 кофе 200"),
            (200.0, "кофе"),
        )

    def test_number_before_price(self):
        self.assertEqual(
            categorizers.parse_expense_message("купил 2 кофе за 300"),
            (300.0, "купил 2 кофе"),
        )

    def test_units_are_not_amounts(self):
        self.assertEqual(
            categorizers.parse_expense_message("такси 450 2 км"),
            (450.0, "такси 2 км"),
        )

    def test_currency_suffix_and_no_space(self):
        self.assertEqual(categorizers.parse_expense_message("500р такси"), (500.0, "такси"))
        self.assertEqual(categorizers.parse_expense_message("такси450"), (450.0, "такси"))

    def test_no_amount(self):
        self.assertIsNone(categorizers.parse_expense_message("просто текст"))


class ParseMultipleTest(unittest.TestCase):
    def test_list_with_comma(self):
        self.assertEqual(
            categorizers.parse_multiple("продукты 800, кофе 200"),
            [(800.0, "продукты"), (200.0, "кофе")],
        )

    def test_list_with_semicolon_and_newline(self):
        self.assertEqual(
            categorizers.parse_multiple("продукты 800; кофе 200\nметро 60"),
            [(800.0, "продукты"), (200.0, "кофе"), (60.0, "метро")],
        )

    def test_decimal_comma_does_not_split(self):
        self.assertEqual(categorizers.parse_multiple("кофе 1,5"), [(1.5, "кофе")])


class CategoryTest(unittest.TestCase):
    def test_word_boundaries(self):
        self.assertEqual(categorizers.guess_category("света перевела 1000"), "Прочее")
        self.assertEqual(categorizers.guess_category("плюшевый мишка 500"), "Прочее")

    def test_longest_phrase_wins(self):
        self.assertEqual(categorizers.guess_category("магазин одежды"), "Одежда")

    def test_known_categories(self):
        self.assertEqual(categorizers.guess_category("аптека 500"), "Здоровье")
        self.assertEqual(categorizers.guess_category("яндекс плюс"), "Подписки")
        self.assertEqual(categorizers.guess_category("метро 60"), "Транспорт")

    def test_user_keyword_has_priority(self):
        rules = [{"keyword": "пятерочка", "category": "Закупки"}]
        self.assertEqual(
            categorizers.guess_category("пятерочка 1200", rules), "Закупки"
        )


class IncomeTest(unittest.TestCase):
    def test_plus(self):
        self.assertEqual(
            categorizers.detect_income("+50000 зарплата"), (True, "50000 зарплата")
        )

    def test_keyword(self):
        self.assertTrue(categorizers.detect_income("зарплата 50000")[0])
        self.assertTrue(categorizers.detect_income("пришло 1000")[0])
        self.assertTrue(categorizers.detect_income("получил зарплату 50000")[0])

    def test_expense_with_similar_word(self):
        self.assertEqual(
            categorizers.detect_income("получил кофе 300"), (False, "получил кофе 300")
        )


if __name__ == "__main__":
    unittest.main()
