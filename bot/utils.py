"""Небольшие общие помощники: время, часовые пояса, формат и нарезка текста."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config

UTC = timezone.utc

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_zone(name: str | None) -> ZoneInfo:
    """Часовой пояс пользователя с откатом на DEFAULT_TIMEZONE."""
    for candidate in (name, config.DEFAULT_TIMEZONE, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def now_local(tz_name: str | None) -> datetime:
    return datetime.now(get_zone(tz_name))


def to_utc_string(dt: datetime, tz_name: str | None) -> str:
    """Naive datetime считаем локальным временем пользователя, храним в UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_zone(tz_name))
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def naive_string_to_utc(value: str, tz_name: str | None) -> str | None:
    """Строка вида '2026-11-05 14:30:00' (локальная) -> UTC-строка."""
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return to_utc_string(dt, tz_name)
        except ValueError:
            continue
    return None


def month_bounds(tz_name: str | None, offset: int = 0) -> tuple[datetime, datetime]:
    """Границы календарного месяца в часовом поясе пользователя.

    offset=0 — текущий месяц, offset=-1 — прошлый.
    """
    tz = get_zone(tz_name)
    now = datetime.now(tz)
    index = now.year * 12 + (now.month - 1) + offset
    year, month = divmod(index, 12)
    month += 1
    start = datetime(year, month, 1, tzinfo=tz)
    next_index = index + 1
    next_year, next_month = divmod(next_index, 12)
    end = datetime(next_year, next_month + 1, 1, tzinfo=tz)
    return start, end


def parse_hhmm(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def fmt_amount(amount: float | int) -> str:
    """Сумма без хвоста .0 и без лишних нулей."""
    value = float(amount)
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").rstrip("0").rstrip(".")


def split_message(text: str, limit: int | None = None) -> list[str]:
    """Режет текст под лимит Telegram, стараясь не разрывать строки/слова."""
    limit = limit or config.MAX_MESSAGE_LEN
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def days_until(deadline: str | None, tz_name: str | None) -> int | None:
    """Сколько дней осталось до даты 'YYYY-MM-DD' (по локальному календарю)."""
    if not deadline:
        return None
    try:
        target = datetime.strptime(deadline, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (target - now_local(tz_name).date()).days


def next_month_start(tz_name: str | None) -> datetime:
    return month_bounds(tz_name, 1)[0]
