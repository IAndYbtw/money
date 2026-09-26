import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import DB_PATH


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                currency TEXT DEFAULT '₽',
                timezone TEXT DEFAULT 'Europe/Moscow',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
                category TEXT NOT NULL,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                target_amount REAL NOT NULL,
                deadline TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_user(tg_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,)
        )


def set_currency(tg_id: int, currency: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET currency = ? WHERE tg_id = ?", (currency, tg_id)
        )


def get_currency(tg_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT currency FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        return row["currency"] if row else "₽"


def add_transaction(tg_id: int, amount: float, kind: str, category: str, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions (tg_id, amount, kind, category, note) VALUES (?, ?, ?, ?, ?)",
            (tg_id, amount, kind, category, note),
        )


def get_transactions(tg_id: int, since: datetime, kind: str = "expense"):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM transactions
            WHERE tg_id = ? AND kind = ? AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (tg_id, kind, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        return rows


def sum_by_category(tg_id: int, since: datetime, kind: str = "expense"):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT category, SUM(amount) as total, COUNT(*) as cnt
            FROM transactions
            WHERE tg_id = ? AND kind = ? AND created_at >= ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (tg_id, kind, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        return rows


def add_goal(tg_id: int, title: str, target_amount: float, deadline: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (tg_id, title, target_amount, deadline) VALUES (?, ?, ?, ?)",
            (tg_id, title, target_amount, deadline),
        )


def get_goals(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE tg_id = ? ORDER BY created_at DESC", (tg_id,)
        ).fetchall()


def all_expenses(tg_id: int, days: int = 120):
    """Для детекта подписок и для сводки в ИИ — берём более широкое окно."""
    since = datetime.now() - timedelta(days=days)
    return get_transactions(tg_id, since, kind="expense")
