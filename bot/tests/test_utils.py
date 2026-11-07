import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils  # noqa: E402


class SplitMessageTest(unittest.TestCase):
    def test_short_text(self):
        self.assertEqual(utils.split_message("привет"), ["привет"])

    def test_empty_text(self):
        self.assertEqual(utils.split_message(""), [])

    def test_long_text_is_chunked(self):
        text = "\n".join(f"строка номер {i}" for i in range(1000))
        chunks = utils.split_message(text, limit=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        for chunk in chunks:
            self.assertTrue(chunk)

    def test_long_word_is_hard_cut(self):
        chunks = utils.split_message("x" * 25, limit=10)
        self.assertEqual([len(chunk) for chunk in chunks], [10, 10, 5])


class MonthBoundsTest(unittest.TestCase):
    def test_months_are_contiguous(self):
        current_start, current_end = utils.month_bounds("Europe/Moscow", 0)
        prev_start, prev_end = utils.month_bounds("Europe/Moscow", -1)
        self.assertEqual(current_start.day, 1)
        self.assertEqual(current_start.hour, 0)
        self.assertEqual(prev_end, current_start)
        self.assertGreater(current_end, current_start)
        self.assertEqual(utils.month_bounds("Europe/Moscow", 1)[0], current_end)

    def test_unknown_zone_falls_back(self):
        self.assertEqual(
            utils.month_bounds("Not/AZone", 0)[0].day,
            1,
        )


class FormatTest(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(utils.fmt_amount(1000), "1 000")
        self.assertEqual(utils.fmt_amount(1.5), "1.5")
        self.assertEqual(utils.fmt_amount(10922.33), "10 922.33")

    def test_hhmm(self):
        self.assertEqual(utils.parse_hhmm("09:00"), (9, 0))
        self.assertEqual(utils.parse_hhmm("9:5"), (9, 5))
        self.assertIsNone(utils.parse_hhmm("25:00"))
        self.assertIsNone(utils.parse_hhmm("abc"))


if __name__ == "__main__":
    unittest.main()
