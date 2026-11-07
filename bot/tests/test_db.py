import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import utils  # noqa: E402


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tmp.name) / "test.sqlite3")
        db.init_db()
        self.user = 42
        db.ensure_user(self.user)

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()


class TransactionTest(DbTestCase):
    def test_manual_duplicates_are_allowed(self):
        db.add_transaction(self.user, 300, "expense", "Продукты", "продукты")
        db.add_transaction(self.user, 300, "expense", "Продукты", "продукты")
        self.assertEqual(len(db.get_transactions(self.user)), 2)

    def test_bulk_import_deduplicates(self):
        created = utils.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        item = {
            "amount": 399,
            "kind": "expense",
            "category": "Подписки",
            "note": "яндекс плюс",
            "created_at": created,
        }
        self.assertEqual(db.add_transactions_bulk(self.user, [item]), (1, 0))
        self.assertEqual(db.add_transactions_bulk(self.user, [item]), (0, 1))

    def test_same_amount_different_dates_is_not_duplicate(self):
        items = [
            {
                "amount": 300,
                "kind": "expense",
                "category": "Продукты",
                "note": "продукты",
                "created_at": "2026-11-01 10:00:00",
            },
            {
                "amount": 300,
                "kind": "expense",
                "category": "Продукты",
                "note": "продукты",
                "created_at": "2026-11-02 10:00:00",
            },
        ]
        self.assertEqual(db.add_transactions_bulk(self.user, items), (2, 0))

    def test_undo(self):
        db.add_transaction(self.user, 300, "expense", "Продукты", "продукты")
        db.add_transaction(self.user, 200, "expense", "Такси", "такси")
        last = db.get_last_transaction(self.user)
        self.assertEqual(last["amount"], 200)
        self.assertTrue(db.delete_transaction(self.user, last["id"]))
        self.assertEqual(len(db.get_transactions(self.user)), 1)


class GoalTest(DbTestCase):
    def test_progress_and_delete(self):
        goal_id = db.add_goal(self.user, "отпуск", 100000)
        self.assertTrue(db.add_goal_progress(self.user, goal_id, 25000))
        goal = db.get_goal(self.user, goal_id)
        self.assertEqual(goal["current_amount"], 25000)
        self.assertTrue(db.delete_goal(self.user, goal_id))
        self.assertEqual(db.get_goals(self.user), [])

    def test_other_user_cannot_change_goal(self):
        goal_id = db.add_goal(self.user, "отпуск", 100000)
        self.assertFalse(db.add_goal_progress(999, goal_id, 5000))
        self.assertFalse(db.delete_goal(999, goal_id))


class BudgetTest(DbTestCase):
    def test_upsert_and_case_insensitive_delete(self):
        db.set_budget(self.user, "Продукты", 10000)
        db.set_budget(self.user, "продукты", 12000)
        budgets = db.get_budgets(self.user)
        self.assertEqual(len(budgets), 1)
        self.assertEqual(budgets[0]["limit_amount"], 12000)
        self.assertEqual(db.get_budget(self.user, "ПРОДУКТЫ")["limit_amount"], 12000)
        self.assertTrue(db.remove_budget(self.user, "Продукты"))
        self.assertEqual(db.get_budgets(self.user), [])


class ReminderTest(DbTestCase):
    def test_defaults_and_time(self):
        db.set_reminder(self.user, True)
        reminder = db.get_reminder(self.user)
        self.assertTrue(reminder["enabled"])
        self.assertEqual((reminder["hour"], reminder["minute"]), (9, 0))

        db.set_reminder_time(self.user, 18, 30)
        reminder = db.get_reminder(self.user)
        self.assertEqual((reminder["hour"], reminder["minute"]), (18, 30))
        self.assertTrue(reminder["enabled"])

    def test_due_users_include_timezone(self):
        db.set_reminder(self.user, True)
        users = db.get_reminder_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["tg_id"], self.user)
        self.assertTrue(users[0]["timezone"])


class AiUsageTest(DbTestCase):
    def test_limit(self):
        day = "2026-11-07"
        self.assertTrue(db.try_use_ai(self.user, day, 2))
        self.assertTrue(db.try_use_ai(self.user, day, 2))
        self.assertFalse(db.try_use_ai(self.user, day, 2))
        self.assertTrue(db.try_use_ai(self.user, day, 0))


class CacheTest(DbTestCase):
    def test_key_and_ttl(self):
        db.set_cached_insights(self.user, "key", "result")
        self.assertEqual(db.get_cached_insights(self.user, "key"), "result")
        self.assertIsNone(db.get_cached_insights(self.user, "other"))

        with db.get_conn() as conn:
            conn.execute(
                "UPDATE insights_cache SET created_at = '2020-01-01 00:00:00' "
                "WHERE tg_id = ?",
                (self.user,),
            )
        self.assertIsNone(db.get_cached_insights(self.user, "key"))


class MigrationTest(unittest.TestCase):
    def test_legacy_schema_is_migrated_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "legacy.sqlite3")
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE users (
                    tg_id INTEGER PRIMARY KEY,
                    currency TEXT DEFAULT '₽',
                    timezone TEXT DEFAULT 'Europe/Moscow',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    kind TEXT NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    target_amount REAL NOT NULL,
                    deadline TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE reminders (
                    tg_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            conn.execute("INSERT INTO users (tg_id) VALUES (42)")
            conn.execute(
                "INSERT INTO transactions (tg_id, amount, kind, category, note) "
                "VALUES (42, 300, 'expense', 'Продукты', 'продукты')"
            )
            conn.execute(
                "INSERT INTO goals (tg_id, title, target_amount) "
                "VALUES (42, 'отпуск', 100000)"
            )
            conn.commit()
            conn.close()

            old_path = db.DB_PATH
            db.DB_PATH = path
            try:
                db.init_db()
                self.assertEqual(len(db.get_transactions(42)), 1)
                goal = db.get_goals(42)[0]
                self.assertEqual(goal["current_amount"], 0)
                self.assertEqual(db.get_reminder_state(42), False)
                with db.get_conn() as check:
                    columns = {
                        row["name"]
                        for row in check.execute("PRAGMA table_info(transactions)")
                    }
                self.assertIn("fingerprint", columns)
            finally:
                db.DB_PATH = old_path


class PrivacyTest(DbTestCase):
    def test_delete_all_user_data(self):
        db.add_transaction(self.user, 300, "expense", "Продукты", "продукты")
        db.add_goal(self.user, "отпуск", 100000)
        db.set_budget(self.user, "Продукты", 10000)
        db.add_history(self.user, "user", "вопрос")
        db.set_reminder(self.user, True)

        db.delete_user_data(self.user)

        self.assertEqual(db.get_transactions(self.user), [])
        self.assertEqual(db.get_goals(self.user), [])
        self.assertEqual(db.get_budgets(self.user), [])
        self.assertEqual(db.get_history(self.user), [])
        self.assertIsNone(db.get_reminder(self.user))
        self.assertEqual(db.get_currency(self.user), "₽")


if __name__ == "__main__":
    unittest.main()
