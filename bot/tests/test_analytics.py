import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analytics  # noqa: E402
import db  # noqa: E402
import utils  # noqa: E402


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tmp.name) / "test.sqlite3")
        db.init_db()
        self.user = 7
        db.ensure_user(self.user)
        self.tz = db.get_timezone(self.user)
        self.now = utils.utcnow()

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def add(self, amount, kind, category, note, when):
        db.add_transaction(
            self.user,
            amount,
            kind,
            category,
            note,
            created_at=when.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def prev_month_date(self):
        start = utils.month_bounds(self.tz, -1)[0]
        return start + timedelta(days=2)

    def test_month_income_balance_and_savings(self):
        self.add(100, "expense", "Продукты", "продукты", self.now)
        self.add(50, "expense", "Транспорт", "такси", self.now)
        self.add(1000, "income", "Доход", "зарплата", self.now)

        summary = analytics.build_ai_summary(self.user)
        self.assertEqual(summary["month"]["total"], 150.0)
        self.assertEqual(summary["month_income"]["total"], 1000.0)
        self.assertEqual(summary["month_balance"], 850.0)
        self.assertEqual(summary["savings_rate_pct"], 85)

    def test_month_over_month_includes_vanished_category(self):
        self.add(100, "expense", "Продукты", "продукты", self.now)
        self.add(50, "expense", "Транспорт", "такси", self.now)
        self.add(200, "expense", "Продукты", "продукты", self.prev_month_date())
        self.add(40, "expense", "Развлечения", "кино", self.prev_month_date())

        changes = {item["category"]: item for item in analytics.month_over_month(self.user)}
        self.assertEqual(changes["Продукты"]["current"], 100.0)
        self.assertEqual(changes["Продукты"]["previous"], 200.0)
        self.assertEqual(changes["Продукты"]["change_pct"], -50)
        self.assertEqual(changes["Развлечения"]["current"], 0.0)
        self.assertEqual(changes["Развлечения"]["change_pct"], -100)
        self.assertIsNone(changes["Транспорт"]["change_pct"])

    def test_budget_status(self):
        self.add(100, "expense", "Продукты", "продукты", self.now)
        db.set_budget(self.user, "Продукты", 80)
        db.set_budget(self.user, "Такси", 500)

        statuses = {item["category"]: item for item in analytics.budget_status(self.user)}
        self.assertTrue(statuses["Продукты"]["over"])
        self.assertEqual(statuses["Продукты"]["remaining"], -20.0)
        self.assertEqual(statuses["Продукты"]["used_pct"], 125)
        self.assertFalse(statuses["Такси"]["over"])
        self.assertEqual(statuses["Такси"]["remaining"], 500.0)

    def test_goal_progress(self):
        deadline = (self.now + timedelta(days=30)).strftime("%Y-%m-%d")
        goal_id = db.add_goal(self.user, "отпуск", 30000, deadline)
        db.add_goal_progress(self.user, goal_id, 3000)

        goal = analytics.goals_progress(self.user)[0]
        self.assertEqual(goal["progress_pct"], 10)
        self.assertEqual(goal["remaining"], 27000.0)
        self.assertIsNotNone(goal["monthly_needed"])

    def test_detect_subscriptions(self):
        for days_ago in (90, 60, 30):
            when = utils.utcnow() - timedelta(days=days_ago)
            self.add(399, "expense", "Подписки", "яндекс плюс", when)

        subscriptions = analytics.detect_subscriptions(self.user)
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0]["name"], "яндекс плюс")
        self.assertEqual(subscriptions[0]["period_days"], 30)
        self.assertTrue(subscriptions[0]["likely_active"])

    def test_similar_amounts_are_grouped(self):
        # 399 и 400 должны попадать в один кластер (допуск по сумме).
        for days_ago, amount in ((60, 399), (30, 400)):
            when = utils.utcnow() - timedelta(days=days_ago)
            self.add(amount, "expense", "Подписки", "подписка", when)

        subscriptions = analytics.detect_subscriptions(self.user)
        self.assertEqual(len(subscriptions), 1)


if __name__ == "__main__":
    unittest.main()
