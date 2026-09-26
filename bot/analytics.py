from datetime import datetime, timedelta
from collections import defaultdict

from bot import db


def period_summary(tg_id: int, days: int):
    since = datetime.now() - timedelta(days=days)
    rows = db.sum_by_category(tg_id, since, kind="expense")
    total = sum(r["total"] for r in rows)
    return {
        "total": total,
        "by_category": [
            {"category": r["category"], "total": r["total"], "count": r["cnt"]}
            for r in rows
        ],
    }


def detect_subscriptions(tg_id: int):
    """
    Ищем повторяющиеся платежи: одинаковая (примерно) сумма + похожее описание,
    встречается 2+ раза с интервалом 20-40 дней -> похоже на подписку.
    """
    rows = db.all_expenses(tg_id, days=120)
    groups = defaultdict(list)
    for r in rows:
        key = (round(r["amount"]), r["note"].lower().strip() if r["note"] else r["category"])
        groups[key].append(datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S"))

    subscriptions = []
    for (amount, note), dates in groups.items():
        if len(dates) < 2:
            continue
        dates.sort()
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        if 20 <= avg_gap <= 40:
            last_use = dates[-1]
            days_since = (datetime.now() - last_use).days
            subscriptions.append(
                {
                    "name": note or "неизвестно",
                    "amount": amount,
                    "period_days": round(avg_gap),
                    "days_since_last": days_since,
                }
            )
    return subscriptions


def month_over_month(tg_id: int):
    """Сравнение трат текущего и прошлого месяца по категориям."""
    now_summary = period_summary(tg_id, days=30)
    prev_rows = db.get_transactions(
        tg_id, since=datetime.now() - timedelta(days=60), kind="expense"
    )
    prev_totals = defaultdict(float)
    cutoff = datetime.now() - timedelta(days=30)
    for r in prev_rows:
        created = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
        if created < cutoff:
            prev_totals[r["category"]] += r["amount"]

    changes = []
    for cat in now_summary["by_category"]:
        prev = prev_totals.get(cat["category"], 0)
        if prev > 0:
            change_pct = round((cat["total"] - prev) / prev * 100)
        else:
            change_pct = None
        changes.append(
            {
                "category": cat["category"],
                "current": cat["total"],
                "previous": prev,
                "change_pct": change_pct,
            }
        )
    return changes


def build_ai_summary(tg_id: int) -> dict:
    """
    Агрегированный JSON для передачи в ИИ.
    Никаких сырых данных карт/счетов — только суммы, категории, даты.
    """
    return {
        "week": period_summary(tg_id, days=7),
        "month": period_summary(tg_id, days=30),
        "month_over_month": month_over_month(tg_id),
        "subscriptions": detect_subscriptions(tg_id),
        "currency": db.get_currency(tg_id),
    }
