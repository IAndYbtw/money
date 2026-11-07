"""Разбор текста траты и автокатегоризация.

Правила разбора суммы:
- поддерживаются "300", "1 500", "1 500,50", "1,5";
- даты ("05.11", "2026-11-05") из текста выкидываются до поиска суммы;
- если чисел несколько, побеждает число с валютой, затем число в начале
  сообщения ("300 продукты"), иначе последнее число ("купил кофе за 300");
- числа с единицами измерения ("2 км", "500 г") суммой не считаются.
"""

import re

CATEGORY_KEYWORDS = {
    "Еда вне дома": [
        "кофе", "ресторан", "кафе", "обед", "бар", "старбакс", "доставка еды",
        "суши", "пицца", "шаурма", "фастфуд", "завтрак",
    ],
    "Продукты": [
        "продукты", "магазин", "супермаркет", "пятерочка", "перекресток",
        "ашан", "лента", "магнит", "вкусвилл",
    ],
    "Транспорт": [
        "такси", "метро", "автобус", "бензин", "заправка", "каршеринг",
        "яндекс go", "убер", "электричка", "проезд",
    ],
    "Подписки": [
        "подписка", "netflix", "spotify", "яндекс плюс", "icloud",
        "youtube premium", "apple music", "кинопоиск",
    ],
    "Развлечения": ["кино", "игра", "концерт", "театр", "клуб", "боулинг"],
    "Здоровье": [
        "аптека", "врач", "стоматолог", "лекарства", "спортзал", "фитнес",
        "анализы", "клиника",
    ],
    "ЖКХ и связь": [
        "жкх", "коммуналка", "интернет", "связь", "мобильный", "свет",
        "отопление", "вода", "аренда", "квартира", "ипотека",
    ],
    "Одежда": ["одежда", "обувь", "zara", "h&m", "магазин одежды"],
    "Образование": ["курсы", "книги", "учеба", "обучение", "школа", "университет"],
}

# Даты: 05.11, 5/11/2026, 2026-11-05. Кандидаты дополнительно проверяются
# на реальные день/месяц, чтобы "1.500" не считалось датой.
DATE_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{2}(?:[./-]\d{2,4})?)(?!\d)"
)

# Число: пробельные разряды тысяч, точки-разряды или обычная запись.
NUMBER_RE = re.compile(
    r"(?<![\w.,])("
    r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?"
    r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
    r"|\d+(?:[.,]\d{1,2})?"
    r")(?!\d)"
)

CURRENCY_RE = re.compile(
    r"^\s*(?:₽|₴|\$|€|руб(?:\.|лей|\b)|р\.?|rub\b|грн\b)", re.IGNORECASE
)

FALLBACK_NUMBER_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d{1,2})?)(?!\d)")
UNIT_RE = re.compile(
    r"^\s*(?:км|м|мм|см|г|гр|кг|шт|л|мл|ч|час|мин|%|раз|чел|дн|день|дней)\b",
    re.IGNORECASE,
)
PREPOSITION_EDGE_RE = re.compile(r"^(?:за|на|по|в|из|от|и)\s+|\s+(?:за|на|по|в|из|от|и)$", re.IGNORECASE)

INCOME_START_STEMS = (
    "зарплат", "аванс", "преми", "доход", "приш", "начислен", "кэшбэк",
    "кэшбек", "возврат", "подработ", "дивиденд",
)
INCOME_STEMS = (
    "зарплат", "аванс", "преми", "кэшбэк", "кэшбек", "начислен",
    "дивиденд", "подработ",
)


def parse_amount_value(text: str | None) -> float | None:
    """'1 500,50' -> 1500.5. Понимает пробелы, запятую и точку."""
    if text is None:
        return None
    value = str(text).strip()
    value = re.sub(r"[^\d,.\s]", "", value)
    value = value.replace("\u00a0", "").replace(" ", "")
    if not value:
        return None

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        # "1.500" в банковских выписках — это 1500, а не 1.5.
        value = value.replace(".", "")

    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _strip_dates(text: str) -> str:
    """Убирает из текста даты, оставляя числа вроде 1.500 и 1.5."""

    def replace(match: re.Match) -> str:
        token = match.group(0)
        parts = re.split(r"[./-]", token)
        try:
            if len(parts) == 3:
                if len(parts[0]) == 4:
                    _, month, day = (int(part) for part in parts)
                else:
                    day, month, _ = (int(part) for part in parts)
            else:
                day, month = (int(part) for part in parts)
        except ValueError:
            return token
        if 1 <= month <= 12 and 1 <= day <= 31:
            return " "
        return token

    return DATE_CANDIDATE_RE.sub(replace, text)


def _is_word(text: str, keyword: str) -> bool:
    """Совпадение по границам слова, чтобы 'свет' не ловил 'Света'."""
    pattern = r"(?<!\w)" + re.escape(keyword.lower()) + r"(?!\w)"
    return re.search(pattern, text.lower()) is not None


def _is_stem(text: str, stem: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(stem.lower())
    return re.search(pattern, text.lower()) is not None


def guess_category(text: str, user_keywords=None) -> str:
    """Сначала правила пользователя, затем общий словарь. Длинное совпадение важнее."""
    text = text or ""

    if user_keywords:
        matched = []
        for row in user_keywords:
            keyword = row["keyword"]
            if _is_word(text, keyword):
                matched.append((len(keyword), row["category"]))
        if matched:
            return max(matched, key=lambda item: item[0])[1]

    matched = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if _is_word(text, keyword):
                matched.append((len(keyword), category))
    if matched:
        return max(matched, key=lambda item: item[0])[1]
    return "Прочее"


def detect_income(text: str) -> tuple[bool, str]:
    """Возвращает (это_доход, текст_без_плюса)."""
    raw = (text or "").strip()
    if raw.startswith("+"):
        return True, raw[1:].strip()

    lowered = raw.lower()
    for stem in INCOME_START_STEMS:
        if _is_stem(lowered, stem):
            return True, raw
    for stem in INCOME_STEMS:
        if _is_stem(lowered, stem):
            return True, raw
    return False, raw


def _find_amount(text: str) -> re.Match | None:
    candidates = []
    for match in NUMBER_RE.finditer(text):
        tail = text[match.end():]
        if UNIT_RE.match(tail):
            continue
        candidates.append(match)
    if not candidates:
        return None

    for match in candidates:
        if CURRENCY_RE.match(text[match.end():]):
            return match

    # "купил 2 кофе за 300" — число после "за/на/стоит" важнее первого.
    for match in candidates:
        prefix = text[: match.start()].lower().rstrip()
        if re.search(r"(?:^|\s)(?:за|на|стоит|цена|итого)$", prefix):
            return match

    first = candidates[0]
    if text[: first.start()].strip() == "":
        return first

    return candidates[-1]


def parse_expense_message(text: str):
    """Возвращает (amount, note) или None, если сумму найти не удалось."""
    text = _strip_dates(text or "")
    match = _find_amount(text)
    if not match:
        # Запасной вариант для слитного написания: "такси450".
        match = FALLBACK_NUMBER_RE.search(text)
    if not match:
        return None

    amount = parse_amount_value(match.group(1))
    if amount is None:
        return None

    note = (text[: match.start()] + " " + text[match.end():]).strip()
    note = re.sub(r"\s+", " ", note)
    # Валюта могла стоять сразу после суммы: "500р такси".
    note = CURRENCY_RE.sub("", note).strip()
    note = re.sub(r"\s*[,;]\s*$", "", note)
    note = PREPOSITION_EDGE_RE.sub("", note).strip()
    note = PREPOSITION_EDGE_RE.sub("", note).strip()
    return amount, note


def parse_multiple(text: str):
    """Разбор списка: 'продукты 800, кофе 200' -> [(800, 'продукты'), (200, 'кофе')].

    Запятая считается разделителем только если после неё не число,
    поэтому 'кофе 1,5' остаётся одной тратой.
    """
    parts = re.split(r"[;\n]+|,\s*(?=[^\d\s])", text or "")
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        parsed = parse_expense_message(part)
        if parsed:
            results.append(parsed)
    return results
