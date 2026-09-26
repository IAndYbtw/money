import re

CATEGORY_KEYWORDS = {
    "Еда вне дома": ["кофе", "ресторан", "кафе", "обед", "бар", "старбакс", "доставка еды", "суши", "пицца"],
    "Продукты": ["продукты", "магазин", "супермаркет", "пятерочка", "перекресток", "ашан", "лента"],
    "Транспорт": ["такси", "метро", "автобус", "бензин", "заправка", "каршеринг", "яндекс go", "убер"],
    "Подписки": ["подписка", "netflix", "spotify", "яндекс плюс", "плюс", "icloud", "youtube premium"],
    "Развлечения": ["кино", "игра", "концерт", "театр", "бар", "клуб"],
    "Здоровье": ["аптека", "врач", "стоматолог", "лекарства", "спортзал", "фитнес"],
    "ЖКХ и связь": ["жкх", "коммуналка", "интернет", "связь", "мобильный", "свет", "отопление"],
    "Одежда": ["одежда", "обувь", "zara", "h&m", "магазин одежды"],
}

# распознаём сумму: "500 такси", "такси 500", "500р такси", "кофе 200р"
AMOUNT_RE = re.compile(r"(\d+[.,]?\d*)\s*(?:р|руб|₽|грн|₴|\$|€)?")


def guess_category(text: str) -> str:
    text_low = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_low:
                return category
    return "Прочее"


def parse_expense_message(text: str):
    """
    Возвращает (amount, note) или None, если сумму найти не удалось.
    Работает и с "500 такси", и с "такси 500".
    """
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    amount_str = match.group(1).replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    if amount <= 0:
        return None

    note = (text[: match.start()] + " " + text[match.end():]).strip()
    note = re.sub(r"\s+", " ", note).strip()
    return amount, note


def parse_multiple(text: str):
    """
    Разбор списком: "продукты 800, кофе 200, метро 60" -> список (amount, note)
    """
    parts = re.split(r"[,\n;]+", text)
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        parsed = parse_expense_message(part)
        if parsed:
            results.append(parsed)
    return results

