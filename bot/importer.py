"""Парсинг банковских выписок в CSV.

Поддерживает два варианта:

1. Простой формат (без заголовка): каждая строка — сумма и описание через
   разделитель, например:
       300, кофе
       -1500, яндекс плюс
   Отрицательная сумма = расход, положительная = доход.

2. С заголовком: первая строка содержит названия колонок. Распознаются:
   сумма (amount/sum/сумма), описание (note/description/описание/назначение),
   дата (date/дата) и тип операции (kind/type/тип/операция).

На выходе — список dict: {"amount", "kind", "note", "date"}.
kind = "expense" | "income"; amount — всегда положительный; date — "%Y-%m-%d %H:%M:%S" или None.
"""

import csv
import io
import re
from datetime import datetime

from categorizers import parse_amount_value

AMOUNT_HEADERS = {
    "amount", "sum", "summa", "сумма", "сумма операции", "amount_rub",
    "сумма_операции", "сумма в валюте счёта", "сумма в валюте счета", "сумма счёта",
}
NOTE_HEADERS = {
    "note", "description", "desc", "описание", "назначение",
    "назначение платежа", "mcc", "категория", "category", "контрагент",
}
DATE_HEADERS = {"date", "дата", "дата операции", "дата_операции", "datetime", "дата и время"}
KIND_HEADERS = {"kind", "type", "тип", "операция", "direction", "приход/расход", "доход/расход", "тип операции"}
DEBIT_HEADERS = {"расход", "списание", "дебет", "debit", "сумма списания", "расход (руб)"}
CREDIT_HEADERS = {"приход", "зачисление", "кредит", "credit", "сумма зачисления", "приход (руб)"}

INCOME_WORDS = ("доход", "приход", "income", "пополнение", "зачисление", "зарплата", "кэшбэк", "cashback")
EXPENSE_WORDS = ("расход", "expense", "списание", "покупка", "оплата", "перевод")

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
)


def parse_amount(cell: str | None) -> float | None:
    if cell is None:
        return None
    t = str(cell).strip().replace("−", "-").replace("–", "-")
    if not t:
        return None
    negative = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    magnitude = parse_amount_value(t)
    if magnitude is None:
        return None
    return -magnitude if negative else magnitude


def detect_kind(amount: float, kind_cell: str | None = None, note: str = "") -> str | None:
    """Тип операции: по колонке kind, иначе по знаку суммы."""
    if kind_cell:
        k = kind_cell.lower()
        if any(w in k for w in INCOME_WORDS):
            return "income"
        if any(w in k for w in EXPENSE_WORDS):
            return "expense"
    if amount < 0:
        return "expense"
    if amount > 0:
        return "income"
    return None


def normalize_date(cell: str | None) -> str | None:
    if not cell:
        return None
    s = str(cell).strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _make_result(amount: float, kind: str | None, note: str, date_cell: str | None) -> dict | None:
    if amount is None or kind is None or amount == 0:
        return None
    return {
        "amount": abs(amount),
        "kind": kind,
        "note": note.strip(),
        "date": normalize_date(date_cell),
    }


def parse_statement(text: str) -> list[dict]:
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return []

    sample = text[:8192]
    first_line = next((line for line in sample.splitlines() if line.strip()), "")
    reader_kwargs = {}
    # Русские банки обычно используют ';' с десятичной запятой — не доверяем
    # снифферу, который может принять запятую в сумме за разделитель колонок.
    if ";" in first_line:
        reader_kwargs["delimiter"] = ";"
    elif "\t" in first_line:
        reader_kwargs["delimiter"] = "\t"
    else:
        try:
            reader_kwargs["dialect"] = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            reader_kwargs["dialect"] = csv.excel

    rows = list(csv.reader(io.StringIO(text), **reader_kwargs))
    rows = [r for r in rows if any(c and c.strip() for c in r)]
    if not rows:
        return []

    header_idx = None
    for i, row in enumerate(rows[:10]):
        lowered = {c.strip().lower() for c in row}
        if lowered & (AMOUNT_HEADERS | DEBIT_HEADERS | CREDIT_HEADERS):
            header_idx = i
            break

    if header_idx is None:
        return _parse_positional(rows)
    return _parse_with_header(rows, header_idx)


def _parse_positional(rows: list[list[str]]) -> list[dict]:
    """Без заголовка: первая числовая ячейка в строке = сумма, остальное = описание/дата."""
    results = []
    for row in rows:
        amount = None
        note_parts = []
        date_cell = None
        for cell in row:
            cell = cell.strip()
            if not cell:
                continue
            if date_cell is None and normalize_date(cell) is not None:
                date_cell = cell
                continue
            if amount is None:
                parsed = parse_amount(cell)
                if parsed is not None:
                    amount = parsed
                    continue
            note_parts.append(cell)

        if amount is None:
            continue
        note = " ".join(note_parts)
        kind = detect_kind(amount, None, note)
        item = _make_result(amount, kind, note, date_cell)
        if item:
            results.append(item)
    return results


def _parse_with_header(rows: list[list[str]], header_idx: int) -> list[dict]:
    header = rows[header_idx]
    lowered = [c.strip().lower() for c in header]

    def find(headers):
        for i, name in enumerate(lowered):
            if name in headers:
                return i
        return None

    amount_i = find(AMOUNT_HEADERS)
    note_i = find(NOTE_HEADERS)
    date_i = find(DATE_HEADERS)
    kind_i = find(KIND_HEADERS)
    debit_i = find(DEBIT_HEADERS)
    credit_i = find(CREDIT_HEADERS)

    if amount_i is None and debit_i is None and credit_i is None:
        return []

    results = []
    for row in rows[header_idx + 1:]:
        def cell(idx):
            return row[idx].strip() if idx is not None and idx < len(row) else None

        note = cell(note_i) or ""
        date_cell = cell(date_i)

        if debit_i is not None or credit_i is not None:
            debit = parse_amount(cell(debit_i)) if debit_i is not None else None
            credit = parse_amount(cell(credit_i)) if credit_i is not None else None
            if debit is not None and debit != 0:
                item = _make_result(abs(debit), "expense", note, date_cell)
                if item:
                    results.append(item)
            elif credit is not None and credit != 0:
                item = _make_result(abs(credit), "income", note, date_cell)
                if item:
                    results.append(item)
            continue

        amount = parse_amount(cell(amount_i))
        if amount is None:
            continue
        kind = detect_kind(amount, cell(kind_i), note)
        item = _make_result(amount, kind, note, date_cell)
        if item:
            results.append(item)
    return results


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def parse_statement_pdf(data: bytes) -> list[dict]:
    """
    Best-effort разбор PDF-выписки: извлекает таблицы (через pdfplumber),
    а если таблиц нет — парсит текстовые строки. Формат банковских PDF разный,
    поэтому результат не гарантирован — при возможности используй CSV.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError(
            "Для PDF нужен pdfplumber: pip install pdfplumber"
        ) from exc

    results: list[dict] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            found_any = False
            for table in tables:
                items = _table_to_items(table)
                results.extend(items)
                if items:
                    found_any = True
            if not found_any:
                results.extend(_text_to_items(page.extract_text()))
    return results


def _clean(cell) -> str:
    return re.sub(r"\s+", " ", str(cell if cell is not None else "")).strip()


def _find_col(lowered: list[str], headers: set[str]) -> int | None:
    for i, name in enumerate(lowered):
        if name in headers:
            return i
    return None


def _table_to_items(table) -> list[dict]:
    rows = []
    for row in table or []:
        if not row:
            continue
        norm = [_clean(c) for c in row]
        if any(norm):
            rows.append(norm)
    if not rows:
        return []

    header_idx = None
    for i, row in enumerate(rows[:5]):
        lowered = [c.lower() for c in row]
        if (
            (set(lowered) & AMOUNT_HEADERS)
            or (set(lowered) & DATE_HEADERS)
            or (set(lowered) & DEBIT_HEADERS)
            or (set(lowered) & CREDIT_HEADERS)
        ):
            header_idx = i
            break

    if header_idx is None:
        return _parse_positional(rows)

    header = rows[header_idx]
    lowered = [c.lower() for c in header]
    date_i = _find_col(lowered, DATE_HEADERS)
    note_i = _find_col(lowered, NOTE_HEADERS)
    kind_i = _find_col(lowered, KIND_HEADERS)
    debit_i = _find_col(lowered, DEBIT_HEADERS)
    credit_i = _find_col(lowered, CREDIT_HEADERS)
    amount_i = _find_col(lowered, AMOUNT_HEADERS)

    results = []
    for row in rows[header_idx + 1:]:
        def cell(idx):
            return row[idx] if idx is not None and idx < len(row) else ""

        note = cell(note_i)
        date_cell = cell(date_i) or None
        kind_cell = cell(kind_i) or None

        if debit_i is not None or credit_i is not None:
            debit = parse_amount(cell(debit_i)) if debit_i is not None else None
            credit = parse_amount(cell(credit_i)) if credit_i is not None else None
            if debit is not None and debit != 0:
                item = _make_result(abs(debit), "expense", note, date_cell)
                if item:
                    results.append(item)
            elif credit is not None and credit != 0:
                item = _make_result(abs(credit), "income", note, date_cell)
                if item:
                    results.append(item)
        else:
            amount = parse_amount(cell(amount_i))
            if amount is None:
                continue
            kind = detect_kind(amount, kind_cell, note)
            item = _make_result(amount, kind, note, date_cell)
            if item:
                results.append(item)
    return results


def _text_to_items(text: str | None) -> list[dict]:
    """Запасной вариант: парсим текстовые строки (дата, сумма, описание)."""
    results = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue

        date_cell = None
        dm = re.search(
            r"\d{2}[./-]\d{2}[./-]\d{4}|\d{4}-\d{2}-\d{2}|\d{2}[./-]\d{2}[./-]\d{2}",
            line,
        )
        if dm:
            date_cell = dm.group()
            line = (line[: dm.start()] + " " + line[dm.end():]).strip()

        am = re.search(
            r"(-?\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|рублей|RUB|р\.)", line
        )
        if not am:
            am = re.search(r"(-?\d[\d\s]*(?:[.,]\d{1,2})?)\s*$", line)
        if not am:
            continue

        amount = parse_amount(am.group(1))
        if amount is None:
            continue

        note = re.sub(r"\s+", " ", (line[: am.start()] + " " + line[am.end():])).strip()
        kind = detect_kind(amount, None, note)
        item = _make_result(amount, kind, note, date_cell)
        if item:
            results.append(item)
    return results
