"""SQLite-слой.

Соглашения:
- всё время в БД хранится в UTC (строки '%Y-%m-%d %H:%M:%S');
- наружу отдаём sqlite3.Row;
- соединение на вызов, WAL + busy_timeout, чтобы не ловить 'database is locked';
- схема обновляется миграциями при старте (init_db).
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import DB_PATH, DEFAULT_REMINDER_TIME, DEFAULT_TIMEZONE

UTC = timezone.utc


def _utc_string(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def normalize_note(note: str | None) -> str:
    return " ".join((note or "").lower().split())


def make_fingerprint(
    tg_id: int,
    amount: float,
    kind: str,
    note: str | None,
    created_at: str | None,
) -> str:
    """Отпечаток импортированной операции для защиты от повторной загрузки."""
    raw = "|".join(
        [
            str(tg_id),
            f"{float(amount):.2f}",
            kind,
            normalize_note(note),
            created_at or "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def init_db() -> None:
    if DB_PATH != ":memory:":
        from pathlib import Path

        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                currency TEXT NOT NULL DEFAULT '₽',
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
                category TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                fingerprint TEXT
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                target_amount REAL NOT NULL,
                current_amount REAL NOT NULL DEFAULT 0,
                deadline TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                category TEXT NOT NULL,
                UNIQUE(tg_id, keyword)
            );

            CREATE TABLE IF NOT EXISTS reminders (
                tg_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                weekday INTEGER NOT NULL DEFAULT 0,
                hour INTEGER NOT NULL DEFAULT 9,
                minute INTEGER NOT NULL DEFAULT 0,
                last_sent_date TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS insights_cache (
                tg_id INTEGER PRIMARY KEY,
                summary_key TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                category_key TEXT NOT NULL,
                limit_amount REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_usage (
                tg_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tg_id, day)
            );
            """
        )
        _migrate(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_tx_user_created
                ON transactions(tg_id, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_fingerprint
                ON transactions(tg_id, fingerprint)
                WHERE fingerprint IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_history_user
                ON chat_history(tg_id, id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_key
                ON budgets(tg_id, category_key);
            """
        )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Догоняем схему старых баз, созданных до этих изменений."""
    _add_column(conn, "users", "timezone", f"TEXT NOT NULL DEFAULT '{DEFAULT_TIMEZONE}'")
    _add_column(conn, "transactions", "fingerprint", "TEXT")
    _add_column(conn, "goals", "current_amount", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "reminders", "weekday", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "reminders", "hour", "INTEGER NOT NULL DEFAULT 9")
    _add_column(conn, "reminders", "minute", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "reminders", "last_sent_date", "TEXT")
    _add_column(conn, "budgets", "category_key", "TEXT")

    # Бэкфилл ключа и удаление дублей по регистру (SQLite lower() не знает кириллицу).
    rows = conn.execute("SELECT id, tg_id, category FROM budgets").fetchall()
    seen: set[tuple[int, str]] = set()
    for row in rows:
        key = (row["tg_id"], (row["category"] or "").strip().lower())
        if key in seen:
            conn.execute("DELETE FROM budgets WHERE id = ?", (row["id"],))
            continue
        seen.add(key)
        conn.execute(
            "UPDATE budgets SET category_key = ? WHERE id = ?",
            (key[1], row["id"]),
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Пользователь
# --------------------------------------------------------------------------- #
def ensure_user(tg_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (tg_id, timezone) VALUES (?, ?)",
            (tg_id, DEFAULT_TIMEZONE),
        )


def set_currency(tg_id: int, currency: str) -> None:
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


def set_timezone(tg_id: int, timezone_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE tg_id = ?", (timezone_name, tg_id)
        )


def get_timezone(tg_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT timezone FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        return row["timezone"] if row and row["timezone"] else DEFAULT_TIMEZONE


# --------------------------------------------------------------------------- #
# Транзакции
# --------------------------------------------------------------------------- #
def add_transaction(
    tg_id: int,
    amount: float,
    kind: str,
    category: str,
    note: str = "",
    created_at: str | None = None,
    fingerprint: str | None = None,
) -> None:
    with get_conn() as conn:
        if created_at:
            conn.execute(
                "INSERT INTO transactions "
                "(tg_id, amount, kind, category, note, created_at, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tg_id, amount, kind, category, note, created_at, fingerprint),
            )
        else:
            conn.execute(
                "INSERT INTO transactions "
                "(tg_id, amount, kind, category, note, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_id, amount, kind, category, note, fingerprint),
            )


def add_transactions_bulk(tg_id: int, items: list[dict]) -> tuple[int, int]:
    """Импорт одной транзакцией и с дедупликацией.

    Каждый item: amount, kind, category, note, created_at (UTC-строка или None).
    Возвращает (вставлено, пропущено_как_дубликаты).
    """
    if not items:
        return 0, 0

    rows = []
    for item in items:
        fingerprint = item.get("fingerprint") or make_fingerprint(
            tg_id,
            item["amount"],
            item["kind"],
            item.get("note"),
            item.get("created_at"),
        )
        rows.append(
            (
                tg_id,
                item["amount"],
                item["kind"],
                item["category"],
                item.get("note") or "",
                item.get("created_at"),
                fingerprint,
            )
        )

    with get_conn() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO transactions "
            "(tg_id, amount, kind, category, note, created_at, fingerprint) "
            "VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)",
            rows,
        )
        inserted = conn.total_changes - before
    return inserted, len(rows) - inserted


def _range_clause(
    tg_id: int,
    since: datetime | None,
    until: datetime | None,
    kind: str | None,
) -> tuple[str, list]:
    sql = "WHERE tg_id = ?"
    params: list = [tg_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if since is not None:
        sql += " AND created_at >= ?"
        params.append(_utc_string(since))
    if until is not None:
        sql += " AND created_at < ?"
        params.append(_utc_string(until))
    return sql, params


def get_transactions(
    tg_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = "expense",
    limit: int | None = None,
):
    where, params = _range_clause(tg_id, since, until, kind)
    sql = f"SELECT * FROM transactions {where} ORDER BY created_at DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def sum_by_category(
    tg_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = "expense",
):
    where, params = _range_clause(tg_id, since, until, kind)
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT category, SUM(amount) AS total, COUNT(*) AS cnt
            FROM transactions {where}
            GROUP BY category
            ORDER BY total DESC
            """,
            params,
        ).fetchall()


def get_last_transaction(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE tg_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (tg_id,),
        ).fetchone()


def delete_transaction(tg_id: int, transaction_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM transactions WHERE tg_id = ? AND id = ?",
            (tg_id, transaction_id),
        )
        return cursor.rowcount > 0


def get_all_transactions(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM transactions WHERE tg_id = ? "
            "ORDER BY created_at, id",
            (tg_id,),
        ).fetchall()


# --------------------------------------------------------------------------- #
# Цели
# --------------------------------------------------------------------------- #
def add_goal(
    tg_id: int,
    title: str,
    target_amount: float,
    deadline: str | None = None,
) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO goals (tg_id, title, target_amount, deadline) "
            "VALUES (?, ?, ?, ?)",
            (tg_id, title, target_amount, deadline),
        )
        return int(cursor.lastrowid)


def get_goals(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE tg_id = ? ORDER BY created_at DESC, id DESC",
            (tg_id,),
        ).fetchall()


def get_goal(tg_id: int, goal_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE tg_id = ? AND id = ?",
            (tg_id, goal_id),
        ).fetchone()


def add_goal_progress(tg_id: int, goal_id: int, amount: float) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE goals SET current_amount = current_amount + ? "
            "WHERE tg_id = ? AND id = ?",
            (amount, tg_id, goal_id),
        )
        return cursor.rowcount > 0


def delete_goal(tg_id: int, goal_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM goals WHERE tg_id = ? AND id = ?", (tg_id, goal_id)
        )
        return cursor.rowcount > 0


# --------------------------------------------------------------------------- #
# Бюджеты
# --------------------------------------------------------------------------- #
def set_budget(tg_id: int, category: str, limit_amount: float) -> None:
    category = category.strip()
    key = category.lower()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO budgets (tg_id, category, category_key, limit_amount) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_id, category_key) DO UPDATE SET "
            "category = excluded.category, "
            "limit_amount = excluded.limit_amount",
            (tg_id, category, key, limit_amount),
        )


def get_budgets(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM budgets WHERE tg_id = ? ORDER BY category",
            (tg_id,),
        ).fetchall()


def get_budget(tg_id: int, category: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM budgets WHERE tg_id = ? AND category_key = ?",
            (tg_id, category.strip().lower()),
        ).fetchone()


def remove_budget(tg_id: int, category: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM budgets WHERE tg_id = ? AND category_key = ?",
            (tg_id, category.strip().lower()),
        )
        return cursor.rowcount > 0


# --------------------------------------------------------------------------- #
# Свои правила категорий
# --------------------------------------------------------------------------- #
def add_user_keyword(tg_id: int, keyword: str, category: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_keywords (tg_id, keyword, category) "
            "VALUES (?, ?, ?)",
            (tg_id, keyword.lower().strip(), category),
        )


def remove_user_keyword(tg_id: int, keyword: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM user_keywords WHERE tg_id = ? AND keyword = ?",
            (tg_id, keyword.lower().strip()),
        )


def get_user_keywords(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT keyword, category FROM user_keywords "
            "WHERE tg_id = ? ORDER BY id",
            (tg_id,),
        ).fetchall()


# --------------------------------------------------------------------------- #
# Напоминания
# --------------------------------------------------------------------------- #
def _default_reminder_time() -> tuple[int, int]:
    hour, _, minute = DEFAULT_REMINDER_TIME.partition(":")
    try:
        return int(hour) % 24, int(minute) % 60
    except ValueError:
        return 9, 0


def set_reminder(tg_id: int, enabled: bool) -> None:
    hour, minute = _default_reminder_time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reminders (tg_id, enabled, hour, minute) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET enabled = excluded.enabled",
            (tg_id, int(enabled), hour, minute),
        )


def get_reminder(tg_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE tg_id = ?", (tg_id,)
        ).fetchone()


def get_reminder_state(tg_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM reminders WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        return bool(row["enabled"]) if row else False


def set_reminder_time(tg_id: int, hour: int, minute: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reminders (tg_id, enabled, hour, minute) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET enabled = 1, "
            "hour = excluded.hour, minute = excluded.minute",
            (tg_id, hour, minute),
        )


def get_reminder_users() -> list:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT r.tg_id, r.weekday, r.hour, r.minute, r.last_sent_date,
                   COALESCE(u.timezone, ?) AS timezone
            FROM reminders r
            LEFT JOIN users u ON u.tg_id = r.tg_id
            WHERE r.enabled = 1
            """,
            (DEFAULT_TIMEZONE,),
        ).fetchall()


def mark_reminder_sent(tg_id: int, date_str: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET last_sent_date = ? WHERE tg_id = ?",
            (date_str, tg_id),
        )


# --------------------------------------------------------------------------- #
# История диалога
# --------------------------------------------------------------------------- #
def add_history(tg_id: int, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_history (tg_id, role, content) VALUES (?, ?, ?)",
            (tg_id, role, content),
        )


def get_history(tg_id: int, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history "
            "WHERE tg_id = ? ORDER BY id DESC LIMIT ?",
            (tg_id, limit),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def clear_history(tg_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_history WHERE tg_id = ?", (tg_id,))


# --------------------------------------------------------------------------- #
# Кэш /insights
# --------------------------------------------------------------------------- #
def get_cached_insights(
    tg_id: int, summary_key: str, ttl_hours: int = 24
) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary_key, result, created_at FROM insights_cache "
            "WHERE tg_id = ?",
            (tg_id,),
        ).fetchone()
    if not row or row["summary_key"] != summary_key:
        return None
    created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    created = created.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > timedelta(hours=ttl_hours):
        return None
    return row["result"]


def set_cached_insights(tg_id: int, summary_key: str, result: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO insights_cache (tg_id, summary_key, result) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET "
            "summary_key = excluded.summary_key, "
            "result = excluded.result, "
            "created_at = datetime('now')",
            (tg_id, summary_key, result),
        )


# --------------------------------------------------------------------------- #
# Лимит запросов к ИИ
# --------------------------------------------------------------------------- #
def try_use_ai(tg_id: int, day: str, limit: int) -> bool:
    """Атомарно проверяет и увеличивает счётчик запросов за день.

    limit <= 0 означает «без ограничения».
    """
    if limit <= 0:
        return True
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM ai_usage WHERE tg_id = ? AND day = ?",
            (tg_id, day),
        ).fetchone()
        used = row["count"] if row else 0
        if used >= limit:
            return False
        conn.execute(
            "INSERT INTO ai_usage (tg_id, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(tg_id, day) DO UPDATE SET count = count + 1",
            (tg_id, day),
        )
        return True


# --------------------------------------------------------------------------- #
# Приватность / экспорт
# --------------------------------------------------------------------------- #
def delete_user_data(tg_id: int) -> None:
    tables = (
        "transactions",
        "goals",
        "user_keywords",
        "reminders",
        "chat_history",
        "insights_cache",
        "budgets",
        "ai_usage",
        "users",
    )
    with get_conn() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE tg_id = ?", (tg_id,))
