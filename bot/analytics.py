"""Аналитика поверх SQLite.

Периоды:
- неделя — скользящие 7 дней;
- месяц/бюджеты/сравнение — календарный месяц в часовом поясе пользователя.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import db
import utils

UTC = timezone.utc

# Слишком общие описания: повторяются у всех, но не являются подписками.
GENERIC_NOTES = {
    "продукты", "еда", "обед", "завтрак", "кофе", "такси", "метро",
    "автобус", "аптека", "магазин", "супермаркет", "перевод",
}
# Категории, где регулярность не означает подписку.
NON_SUBSCRIPTION_CATEGORIES = {"продукты", "еда вне дома", "транспорт", "здоровье"}


def _parse_db_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _rows_summary(rows) -> dict:
    total = sum(row["total"] for row in rows)
    return {
        "total": round(total, 2),
        "by_category": [
            {
                "category": row["category"],
                "total": round(row["total"], 2),
                "count": row["cnt"],
            }
            for row in rows
        ],
    }


def range_summary(
    tg_id: int,
    since: datetime | None,
    until: datetime | None,
    kind: str = "expense",
) -> dict:
    return _rows_summary(db.sum_by_category(tg_id, since, until, kind=kind))


def period_summary(tg_id: int, days: int, kind: str = "expense", tz: str | None = None) -> dict:
    """Скользящее окно: последние `days` дней по локальному времени."""
    since = utils.now_local(tz or db.get_timezone(tg_id)) - timedelta(days=days)
    return range_summary(tg_id, since, None, kind=kind)


def month_summary(tg_id: int, offset: int = 0, kind: str = "expense") -> dict:
    """Календарный месяц: offset=0 — текущий, -1 — прошлый."""
    tz = db.get_timezone(tg_id)
    start, end = utils.month_bounds(tz, offset)
    return range_summary(tg_id, start, end, kind=kind)


def month_over_month(tg_id: int) -> list[dict]:
    """Сравнение текущего и прошлого календарного месяца по категориям."""
    current = {item["category"]: item["total"] for item in month_summary(tg_id)["by_category"]}
    previous = {
        item["category"]: item["total"]
        for item in month_summary(tg_id, offset=-1)["by_category"]
    }

    changes = []
    for category in sorted(set(current) | set(previous)):
        now_value = current.get(category, 0)
        prev_value = previous.get(category, 0)
        if now_value == 0 and prev_value == 0:
            continue
        change_pct = None
        if prev_value > 0:
            change_pct = round((now_value - prev_value) / prev_value * 100)
        changes.append(
            {
                "category": category,
                "current": round(now_value, 2),
                "previous": round(prev_value, 2),
                "change_pct": change_pct,
            }
        )
    changes.sort(key=lambda item: abs(item["current"] - item["previous"]), reverse=True)
    return changes


def detect_subscriptions(tg_id: int) -> list[dict]:
    """Ищем регулярные платежи: похожее описание, близкая сумма, интервал 20-40 дней."""
    tz = db.get_timezone(tg_id)
    since = utils.now_local(tz) - timedelta(days=120)
    rows = db.get_transactions(tg_id, since=since, kind="expense")

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        key = db.normalize_note(row["note"]) or (row["category"] or "").lower()
        if not key or key in GENERIC_NOTES:
            continue
        if (row["category"] or "").lower() in NON_SUBSCRIPTION_CATEGORIES:
            continue
        groups[key].append(row)

    now = utils.utcnow()
    subscriptions = []
    for name, items in groups.items():
        items.sort(key=lambda row: row["created_at"])
        clusters: list[list] = []
        for row in items:
            placed = False
            for cluster in clusters:
                other = cluster[-1]["amount"]
                tolerance = max(2.0, 0.05 * max(other, row["amount"]))
                if abs(other - row["amount"]) <= tolerance:
                    cluster.append(row)
                    placed = True
                    break
            if not placed:
                clusters.append([row])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            dates = [_parse_db_dt(row["created_at"]) for row in cluster]
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_gap = sum(gaps) / len(gaps)
            if not 20 <= avg_gap <= 40:
                continue
            days_since = (now - dates[-1]).days
            subscriptions.append(
                {
                    "name": name,
                    "amount": round(sum(row["amount"] for row in cluster) / len(cluster), 2),
                    "period_days": round(avg_gap),
                    "days_since_last": days_since,
                    "likely_active": days_since <= 45,
                }
            )

    subscriptions.sort(key=lambda item: item["amount"], reverse=True)
    return subscriptions


def budget_status(tg_id: int) -> list[dict]:
    """Сколько потрачено в текущем календарном месяце относительно лимитов."""
    budgets = db.get_budgets(tg_id)
    if not budgets:
        return []
    spent_by_category = {
        item["category"].lower(): item["total"]
        for item in month_summary(tg_id)["by_category"]
    }
    result = []
    for budget in budgets:
        category = budget["category"]
        limit = budget["limit_amount"]
        spent = round(spent_by_category.get(category.lower(), 0), 2)
        remaining = round(limit - spent, 2)
        result.append(
            {
                "category": category,
                "limit": round(limit, 2),
                "spent": spent,
                "remaining": remaining,
                "used_pct": round(spent / limit * 100) if limit > 0 else None,
                "over": spent > limit,
            }
        )
    result.sort(key=lambda item: (item["used_pct"] or 0), reverse=True)
    return result


def goals_progress(tg_id: int) -> list[dict]:
    tz = db.get_timezone(tg_id)
    result = []
    for goal in db.get_goals(tg_id):
        target = goal["target_amount"]
        current = goal["current_amount"] or 0
        remaining = max(target - current, 0)
        days_left = utils.days_until(goal["deadline"], tz)
        monthly_needed = None
        if days_left is not None and days_left > 0:
            monthly_needed = round(remaining / (days_left / 30), 2)
        result.append(
            {
                "id": goal["id"],
                "title": goal["title"],
                "target": round(target, 2),
                "current": round(current, 2),
                "remaining": round(remaining, 2),
                "progress_pct": min(round(current / target * 100), 100) if target > 0 else 0,
                "deadline": goal["deadline"],
                "days_left": days_left,
                "monthly_needed": monthly_needed,
            }
        )
    return result


def build_ai_summary(tg_id: int) -> dict:
    """Агрегированный JSON для ИИ: без номеров карт и сырых получателей."""
    tz = db.get_timezone(tg_id)
    month_expense = month_summary(tg_id, 0, "expense")
    month_income = month_summary(tg_id, 0, "income")
    balance = round(month_income["total"] - month_expense["total"], 2)
    savings_rate = None
    if month_income["total"] > 0:
        savings_rate = round(balance / month_income["total"] * 100)

    return {
        "week": period_summary(tg_id, 7, "expense", tz),
        "week_income": period_summary(tg_id, 7, "income", tz),
        "month": month_expense,
        "month_income": month_income,
        "month_balance": balance,
        "savings_rate_pct": savings_rate,
        "month_over_month": month_over_month(tg_id),
        "subscriptions": detect_subscriptions(tg_id),
        "budgets": budget_status(tg_id),
        "goals": goals_progress(tg_id),
        "currency": db.get_currency(tg_id),
        "timezone": tz,
    }
