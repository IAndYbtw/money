import asyncio
import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import ai
import analytics
import categorizers
import config
import db
import importer
import utils

log = logging.getLogger("finbot")
dp = Dispatcher()

MAX_HISTORY = 10
INSIGHTS_TTL_HOURS = 24
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


# --------------------------------------------------------------------------- #
# Помощники
# --------------------------------------------------------------------------- #
def _args(text: str | None) -> str:
    """Текст после команды: '/ask вопрос' -> 'вопрос'."""
    return (text or "").partition(" ")[2].strip()


def _fmt(amount: float) -> str:
    return utils.fmt_amount(amount)


def _bar(percent: int, width: int = 10) -> str:
    filled = max(0, min(width, round(percent / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def _app_keyboard() -> InlineKeyboardMarkup | None:
    if not config.WEB_APP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть приложение",
                    web_app=WebAppInfo(url=config.WEB_APP_URL),
                )
            ]
        ]
    )


async def _send(message: Message, text: str, **kwargs) -> None:
    """Отправляет длинный текст частями, чтобы не упереться в лимит 4096."""
    for chunk in utils.split_message(text):
        await message.answer(chunk, **kwargs)


async def _send_to(bot: Bot, tg_id: int, text: str) -> None:
    for chunk in utils.split_message(text):
        await bot.send_message(tg_id, chunk)


def _ai_allowed(tg_id: int) -> bool:
    day = utils.now_local(db.get_timezone(tg_id)).strftime("%Y-%m-%d")
    return db.try_use_ai(tg_id, day, config.AI_DAILY_LIMIT)


def _limit_text() -> str:
    return (
        f"Лимит запросов к ИИ на сегодня исчерпан ({config.AI_DAILY_LIMIT}). "
        "Попробуй завтра или увеличь AI_DAILY_LIMIT в .env."
    )


def _ai_error_text(exc: Exception) -> str:
    """Понятное пользователю объяснение вместо общего «попробуй позже»."""
    if not config.API_KEY:
        return (
            "ИИ не настроен: добавь AI_API_KEY в bot/.env и перезапусти бота."
        )
    text = str(exc)
    if "402" in text or "Insufficient Balance" in text:
        return (
            "У ИИ-провайдера нулевой баланс. Пополни счёт или переключись "
            "на бесплатный тариф (см. README, раздел «Бесплатные провайдеры»)."
        )
    if "401" in text or "Authentication" in text:
        return "Ключ ИИ неверный или отозван. Обнови AI_API_KEY в bot/.env."
    if "429" in text or "rate limit" in text.lower():
        return "ИИ временно упёрся в лимит запросов. Попробуй через минуту."
    return "Не получилось связаться с ИИ. Попробуй позже."


def _local_dt(created_at: str, tz_name: str) -> str:
    dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(utils.get_zone(tz_name)).strftime("%d.%m.%Y %H:%M")


def _budget_warnings(tg_id: int, deltas: dict[str, float]) -> list[str]:
    """Предупреждаем в момент, когда трата переводит категорию через лимит."""
    if not deltas:
        return []
    currency = db.get_currency(tg_id)
    statuses = {item["category"].lower(): item for item in analytics.budget_status(tg_id)}
    lines = []
    for category, delta in deltas.items():
        status = statuses.get(category.lower())
        if not status:
            continue
        limit = status["limit"]
        spent = status["spent"]
        if spent >= limit and spent - delta < limit:
            lines.append(
                f"⚠️ Бюджет «{status['category']}» исчерпан: "
                f"{_fmt(spent)} / {_fmt(limit)} {currency}"
            )
    return lines


def _over_budget_lines(tg_id: int) -> list[str]:
    currency = db.get_currency(tg_id)
    return [
        f"⚠️ «{item['category']}»: {_fmt(item['spent'])} / {_fmt(item['limit'])} {currency}"
        for item in analytics.budget_status(tg_id)
        if item["over"]
    ]


# --------------------------------------------------------------------------- #
# Базовые команды
# --------------------------------------------------------------------------- #
@dp.message(Command("start"))
async def command_start_handler(message: Message):
    db.ensure_user(message.from_user.id)
    text = (
        "👋 Привет! Я помогу понять, куда уходят деньги, и найду траты, "
        "которые ты не замечаешь.\n\n"
        "Просто пиши траты: «300 продукты» или «такси 450». "
        "Доход — через «+» или слово «зарплата».\n\n"
        "Команды:\n"
        "/insights — анализ трат\n"
        "/ask <вопрос> — вопрос по своим финансам\n"
        "/goal <сумма> <название> [дата] — новая цель\n"
        "/goals — цели и прогресс\n"
        "/budget <категория> <сумма> — лимит на месяц\n"
        "/budgets — исполнение бюджетов\n"
        "/undo — отменить последнюю запись\n"
        "/import — загрузить выписку\n"
        "/remind — еженедельная сводка\n"
        "/help — полная справка"
    )
    keyboard = _app_keyboard()
    await _send(message, text, reply_markup=keyboard)


@dp.message(Command("help"))
async def command_help_handler(message: Message):
    await _send(
        message,
        "ℹ️ Помощь:\n\n"
        "Записать трату: «300 продукты», «такси 450»\n"
        "Записать доход: «+50000 зарплата»\n"
        "Несколько трат разом: «продукты 800; кофе 200»\n\n"
        "💸 Траты и доходы:\n"
        "/insights — анализ за неделю и месяц\n"
        "/ask <вопрос> — вопрос по своим финансам\n"
        "/undo — удалить последнюю запись\n"
        "/import — загрузить CSV/PDF-выписку\n"
        "/export — выгрузить все операции в CSV\n"
        "/reset — очистить историю диалога с ИИ\n\n"
        "🎯 Цели:\n"
        "/goal <сумма> <название> [ГГГГ-ММ-ДД]\n"
        "/goals — список и прогресс\n"
        "/goaladd <id> <сумма> — отложить на цель\n"
        "/goaldel <id> — удалить цель\n\n"
        "📊 Бюджеты:\n"
        "/budget <категория> <сумма> — лимит на календарный месяц\n"
        "/budgets — сколько потрачено и осталось\n"
        "/delbudget <категория> — удалить лимит\n\n"
        "🏷 Категории и настройки:\n"
        "/cat <слово> <категория> — своё правило\n"
        "/cats — список правил\n"
        "/delcat <слово> — удалить правило\n"
        "/remind on|off|ЧЧ:ММ — еженедельная сводка\n"
        "/timezone <зона> — например Europe/Berlin\n"
        "/currency <знак> — ₽, $, €\n"
        "/deletedata confirm — удалить все мои данные",
    )


@dp.message(Command("app"))
async def command_app(message: Message):
    keyboard = _app_keyboard()
    if not keyboard:
        await message.answer("🚧 Mini App пока в разработке.")
        return
    await message.answer("Открываю приложение…", reply_markup=keyboard)


# --------------------------------------------------------------------------- #
# ИИ
# --------------------------------------------------------------------------- #
@dp.message(Command("insights"))
async def command_insights(message: Message):
    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    summary = analytics.build_ai_summary(tg_id)

    if summary["month"]["total"] == 0 and not summary["subscriptions"]:
        await message.answer(
            "Пока нечего анализировать — запиши хотя бы пару трат, "
            "например «300 продукты»."
        )
        return

    summary_key = hashlib.md5(
        json.dumps(summary, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    cached = db.get_cached_insights(tg_id, summary_key, INSIGHTS_TTL_HOURS)
    if cached:
        await _send(message, cached)
        return

    if not _ai_allowed(tg_id):
        await message.answer(_limit_text())
        return

    await message.answer("🔎 Анализирую твои траты…")
    try:
        text = await asyncio.to_thread(ai.generate_insights, summary)
    except Exception as exc:
        log.exception("AI insights failed for user %s", tg_id)
        await message.answer(_ai_error_text(exc))
        return
    if not text.strip():
        await message.answer("ИИ вернул пустой ответ. Попробуй ещё раз позже.")
        return
    db.set_cached_insights(tg_id, summary_key, text)
    await _send(message, text)


@dp.message(Command("ask"))
async def command_ask(message: Message):
    question = _args(message.text)
    if not question:
        await message.answer(
            "Напиши вопрос после /ask, например: "
            "«/ask почему выросли траты на такси?»"
        )
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    if not _ai_allowed(tg_id):
        await message.answer(_limit_text())
        return

    summary = analytics.build_ai_summary(tg_id)
    history = db.get_history(tg_id, MAX_HISTORY)

    await message.answer("🤔 Думаю…")
    try:
        answer = await asyncio.to_thread(
            ai.answer_question, summary, question, history
        )
    except Exception as exc:
        log.exception("AI ask failed for user %s", tg_id)
        await message.answer(_ai_error_text(exc))
        return
    if not answer.strip():
        await message.answer("ИИ вернул пустой ответ. Попробуй ещё раз позже.")
        return

    db.add_history(tg_id, "user", question)
    db.add_history(tg_id, "assistant", answer)
    await _send(message, answer)


@dp.message(Command("reset"))
async def command_reset(message: Message):
    db.clear_history(message.from_user.id)
    await message.answer("🧹 История диалога с ИИ очищена.")


# --------------------------------------------------------------------------- #
# Цели
# --------------------------------------------------------------------------- #
@dp.message(Command("goal"))
async def command_goal(message: Message):
    tg_id = message.from_user.id
    db.ensure_user(tg_id)

    args = _args(message.text)
    if not args:
        await message.answer(
            "Формат: /goal <сумма> <название> [дата]\n"
            "Пример: /goal 100000 отпуск 2027-06-01"
        )
        return

    tokens = args.split()
    amount = categorizers.parse_amount_value(tokens[0])
    if amount is None:
        await message.answer("Не понял сумму. Пример: /goal 100000 отпуск")
        return

    title_tokens = tokens[1:]
    deadline = None
    if title_tokens and DATE_RE.match(title_tokens[-1]):
        try:
            datetime.strptime(title_tokens[-1], "%Y-%m-%d")
        except ValueError:
            await message.answer("Дата должна быть реальной, формат ГГГГ-ММ-ДД.")
            return
        deadline = title_tokens[-1]
        title_tokens = title_tokens[:-1]

    title = " ".join(title_tokens) if title_tokens else "Без названия"
    goal_id = db.add_goal(tg_id, title, amount, deadline)
    deadline_txt = f" до {deadline}" if deadline else ""
    await message.answer(
        f"🎯 Цель #{goal_id} создана: «{title}» — {_fmt(amount)}{deadline_txt}.\n"
        f"Откладывать: /goaladd {goal_id} <сумма>"
    )


@dp.message(Command("goals"))
async def command_goals(message: Message):
    tg_id = message.from_user.id
    goals = analytics.goals_progress(tg_id)
    if not goals:
        await message.answer("Целей пока нет. Создай: /goal 100000 отпуск")
        return

    currency = db.get_currency(tg_id)
    lines = ["🎯 Твои цели:"]
    for goal in goals:
        lines.append(
            f"\n#{goal['id']} {goal['title']} — "
            f"{_fmt(goal['current'])} / {_fmt(goal['target'])} {currency} "
            f"({goal['progress_pct']}%)"
        )
        lines.append(_bar(goal["progress_pct"]))
        if goal["deadline"]:
            left = goal["days_left"]
            if left is not None and left >= 0:
                lines.append(f"до {goal['deadline']} — осталось {left} дн.")
            else:
                lines.append(f"дедлайн {goal['deadline']} прошёл")
        if goal["monthly_needed"]:
            lines.append(
                f"нужно ~{_fmt(goal['monthly_needed'])} {currency}/мес до дедлайна"
            )
    await _send(message, "\n".join(lines))


@dp.message(Command("goaladd"))
async def command_goaladd(message: Message):
    args = _args(message.text).split()
    if len(args) < 2 or not args[0].isdigit():
        await message.answer("Формат: /goaladd <id> <сумма>\nПример: /goaladd 1 5000")
        return

    tg_id = message.from_user.id
    goal_id = int(args[0])
    amount = categorizers.parse_amount_value(args[1])
    if amount is None:
        await message.answer("Не понял сумму. Пример: /goaladd 1 5000")
        return

    if not db.add_goal_progress(tg_id, goal_id, amount):
        await message.answer(f"Цель #{goal_id} не найдена. Список: /goals")
        return

    goal = db.get_goal(tg_id, goal_id)
    current = goal["current_amount"]
    target = goal["target_amount"]
    percent = min(round(current / target * 100), 100) if target else 0
    currency = db.get_currency(tg_id)
    await message.answer(
        f"✅ Отложено {_fmt(amount)} {currency} на «{goal['title']}».\n"
        f"{_fmt(current)} / {_fmt(target)} {currency} ({percent}%)\n"
        f"{_bar(percent)}"
    )


@dp.message(Command("goaldel"))
async def command_goaldel(message: Message):
    arg = _args(message.text)
    if not arg.isdigit():
        await message.answer("Формат: /goaldel <id>")
        return
    if db.delete_goal(message.from_user.id, int(arg)):
        await message.answer(f"🗑 Цель #{arg} удалена.")
    else:
        await message.answer(f"Цель #{arg} не найдена.")


# --------------------------------------------------------------------------- #
# Бюджеты
# --------------------------------------------------------------------------- #
@dp.message(Command("budget"))
async def command_budget(message: Message):
    args = _args(message.text)
    if " " not in args:
        await message.answer(
            "Формат: /budget <категория> <сумма>\n"
            "Пример: /budget Еда вне дома 15000"
        )
        return

    category, amount_raw = args.rsplit(" ", 1)
    category = category.strip()
    amount = categorizers.parse_amount_value(amount_raw)
    if not category or amount is None:
        await message.answer(
            "Формат: /budget <категория> <сумма>\n"
            "Пример: /budget Еда вне дома 15000"
        )
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    db.set_budget(tg_id, category, amount)
    currency = db.get_currency(tg_id)
    await message.answer(
        f"📊 Бюджет «{category}»: {_fmt(amount)} {currency} в месяц.\n"
        "Смотреть исполнение: /budgets"
    )


@dp.message(Command("budgets"))
async def command_budgets(message: Message):
    tg_id = message.from_user.id
    statuses = analytics.budget_status(tg_id)
    if not statuses:
        await message.answer(
            "Бюджетов пока нет. Добавь: /budget Еда вне дома 15000"
        )
        return

    currency = db.get_currency(tg_id)
    lines = ["📊 Бюджеты на этот месяц:"]
    for item in statuses:
        percent = item["used_pct"] or 0
        lines.append(
            f"\n{item['category']}: {_fmt(item['spent'])} / "
            f"{_fmt(item['limit'])} {currency} ({percent}%)"
        )
        lines.append(_bar(percent))
        if item["over"]:
            lines.append(f"перерасход {_fmt(abs(item['remaining']))} {currency}")
        else:
            lines.append(f"осталось {_fmt(item['remaining'])} {currency}")
    await _send(message, "\n".join(lines))


@dp.message(Command("delbudget"))
async def command_delbudget(message: Message):
    category = _args(message.text)
    if not category:
        await message.answer("Формат: /delbudget <категория>")
        return
    if db.remove_budget(message.from_user.id, category):
        await message.answer(f"🗑 Бюджет «{category}» удалён.")
    else:
        await message.answer(f"Бюджет «{category}» не найден.")


# --------------------------------------------------------------------------- #
# Категории
# --------------------------------------------------------------------------- #
@dp.message(Command("cat"))
async def command_add_category(message: Message):
    args = _args(message.text)
    if not args or " " not in args:
        await message.answer(
            "Формат: /cat <ключевое слово> <категория>\n"
            "Пример: /cat яндекс.такси Транспорт"
        )
        return

    keyword, category = args.split(" ", 1)
    keyword, category = keyword.strip(), category.strip()
    if not keyword or not category:
        await message.answer("Нужны и слово, и категория. Пример: /cat доставка Еда")
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    db.add_user_keyword(tg_id, keyword, category)
    await message.answer(f"Готово: «{keyword}» теперь относится к категории «{category}».")


@dp.message(Command("cats"))
async def command_list_categories(message: Message):
    rules = db.get_user_keywords(message.from_user.id)
    if not rules:
        await message.answer(
            "Своих правил пока нет. Добавь: /cat <слово> <категория>"
        )
        return
    lines = ["🏷 Твои правила категорий:"]
    for rule in rules:
        lines.append(f"• {rule['keyword']} → {rule['category']}")
    await _send(message, "\n".join(lines))


@dp.message(Command("delcat"))
async def command_del_category(message: Message):
    keyword = _args(message.text)
    if not keyword:
        await message.answer("Формат: /delcat <ключевое слово>")
        return
    db.remove_user_keyword(message.from_user.id, keyword)
    await message.answer(f"Удалил правило для «{keyword}», если оно было.")


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #
@dp.message(Command("remind"))
async def command_remind(message: Message):
    arg = _args(message.text).lower()
    tg_id = message.from_user.id
    db.ensure_user(tg_id)

    if arg in ("on", "вкл", "да", "1", "start"):
        db.set_reminder(tg_id, True)
        await message.answer(
            "🔔 Включил еженедельную сводку. Время можно задать: /remind 09:00"
        )
    elif arg in ("off", "выкл", "нет", "0", "stop"):
        db.set_reminder(tg_id, False)
        await message.answer("🔕 Выключил еженедельную сводку.")
    elif (time_value := utils.parse_hhmm(arg)) is not None:
        hour, minute = time_value
        db.set_reminder_time(tg_id, hour, minute)
        await message.answer(
            f"⏰ Сводка будет приходить по понедельникам в {hour:02d}:{minute:02d} "
            f"({db.get_timezone(tg_id)})."
        )
    else:
        state = db.get_reminder(tg_id)
        if not state or not state["enabled"]:
            await message.answer(
                "Сейчас сводка выключена. Включи: /remind on "
                "или задай время: /remind 09:00"
            )
        else:
            await message.answer(
                f"Сводка включена: {WEEKDAYS[state['weekday']]}, "
                f"{state['hour']:02d}:{state['minute']:02d} "
                f"({db.get_timezone(tg_id)}).\n"
                "Выключить: /remind off"
            )


@dp.message(Command("timezone", "tz"))
async def command_timezone(message: Message):
    arg = _args(message.text)
    tg_id = message.from_user.id
    db.ensure_user(tg_id)

    if not arg:
        await message.answer(
            f"Твой часовой пояс: {db.get_timezone(tg_id)}\n"
            "Сменить: /timezone Europe/Berlin"
        )
        return
    try:
        zone = ZoneInfo(arg)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(
            "Не знаю такой часовой пояс. Примеры: Europe/Moscow, Europe/Berlin, "
            "Asia/Almaty, UTC."
        )
        return

    db.set_timezone(tg_id, arg)
    local = datetime.now(zone).strftime("%H:%M")
    await message.answer(f"🕒 Часовой пояс: {arg} (сейчас {local}).")


@dp.message(Command("currency"))
async def command_currency(message: Message):
    symbol = _args(message.text)
    if not symbol or len(symbol) > 5:
        await message.answer("Формат: /currency <знак>\nПример: /currency $")
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    db.set_currency(tg_id, symbol)
    await message.answer(f"Валюта изменена на {symbol}")


# --------------------------------------------------------------------------- #
# Undo / экспорт / приватность
# --------------------------------------------------------------------------- #
@dp.message(Command("undo"))
async def command_undo(message: Message):
    tg_id = message.from_user.id
    row = db.get_last_transaction(tg_id)
    if not row:
        await message.answer("Нечего отменять — записей пока нет.")
        return

    db.delete_transaction(tg_id, row["id"])
    currency = db.get_currency(tg_id)
    kind = "доход" if row["kind"] == "income" else "трата"
    note = f" ({row['note']})" if row["note"] else ""
    local_dt = _local_dt(row["created_at"], db.get_timezone(tg_id))
    await message.answer(
        f"↩️ Удалил последнюю запись: {_fmt(row['amount'])} {currency} "
        f"({kind}){note}\n{row['category']}, {local_dt}"
    )


@dp.message(Command("export"))
async def command_export(message: Message):
    tg_id = message.from_user.id
    rows = db.get_all_transactions(tg_id)
    if not rows:
        await message.answer("Пока нечего выгружать — записей нет.")
        return

    tz = db.get_timezone(tg_id)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["date", "kind", "amount", "category", "note"])
    for row in rows:
        writer.writerow(
            [
                _local_dt(row["created_at"], tz),
                row["kind"],
                f"{row['amount']:.2f}",
                row["category"],
                row["note"] or "",
            ]
        )

    data = buffer.getvalue().encode("utf-8-sig")
    await message.answer_document(
        BufferedInputFile(data, filename="finbot_export.csv"),
        caption=f"📤 {len(rows)} операций",
    )


@dp.message(Command("deletedata"))
async def command_deletedata(message: Message):
    if _args(message.text).lower() != "confirm":
        await message.answer(
            "⚠️ Это удалит все твои траты, цели, бюджеты и историю.\n"
            "Если точно хочешь — отправь: /deletedata confirm"
        )
        return
    db.delete_user_data(message.from_user.id)
    await message.answer("🗑 Все твои данные удалены.")


# --------------------------------------------------------------------------- #
# Импорт выписок
# --------------------------------------------------------------------------- #
@dp.message(Command("import"))
async def command_import_help(message: Message):
    await message.answer(
        "📥 Загрузка выписки:\n\n"
        "Отправь CSV или PDF-файл (просто прикрепи документ).\n\n"
        "CSV поддерживает:\n"
        "• простой формат: сумма,описание (например «-300, кофе» — расход)\n"
        "• с заголовками: сумма/описание/дата/тип\n\n"
        "PDF разбирается по возможности (формат банков различается), "
        "надёжнее — CSV.\n\n"
        "Отрицательная сумма = расход, положительная = доход. "
        "Повторная загрузка того же файла не создаст дубликаты."
    )


def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@dp.message(F.document)
async def handle_document(message: Message, bot: Bot):
    doc = message.document
    filename = (doc.file_name or "").lower()
    if not filename.endswith((".csv", ".txt", ".pdf")):
        await message.answer(
            "Пока умею импортировать CSV и PDF. Отправь файл .csv или .pdf "
            "(см. /import)."
        )
        return

    if doc.file_size and doc.file_size > config.MAX_IMPORT_BYTES:
        await message.answer(
            f"Файл слишком большой (максимум {config.MAX_IMPORT_BYTES // (1024 * 1024)} МБ)."
        )
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)

    await message.answer("📥 Читаю файл…")
    try:
        file = await bot.get_file(doc.file_id)
        data = await bot.download_file(file.file_path)
        try:
            raw = data.read()
        finally:
            data.close()
    except Exception:
        log.exception("Failed to download file for user %s", tg_id)
        await message.answer("Не удалось скачать файл. Попробуй ещё раз.")
        return

    try:
        if filename.endswith(".pdf"):
            items = importer.parse_statement_pdf(raw)
        else:
            items = importer.parse_statement(_decode_csv(raw))
    except Exception:
        log.exception("Failed to parse statement for user %s", tg_id)
        await message.answer(
            "Не удалось разобрать файл. Проверь формат (см. /import) — "
            "для PDF нужна библиотека pdfplumber."
        )
        return

    if not items:
        await message.answer(
            "Не нашёл в файле строк с суммой. Проверь формат (см. /import)."
        )
        return

    truncated = False
    if len(items) > config.MAX_IMPORT_ROWS:
        items = items[: config.MAX_IMPORT_ROWS]
        truncated = True

    tz = db.get_timezone(tg_id)
    user_keywords = db.get_user_keywords(tg_id)
    prepared = []
    for item in items:
        created_at = (
            utils.naive_string_to_utc(item["date"], tz) if item["date"] else None
        )
        category = (
            "Доход"
            if item["kind"] == "income"
            else categorizers.guess_category(item["note"], user_keywords)
        )
        prepared.append(
            {
                "amount": item["amount"],
                "kind": item["kind"],
                "category": category,
                "note": item["note"],
                "created_at": created_at,
                # Отпечаток считаем по исходной дате из файла, а не по UTC:
                # смена часового пояса не должна превращать импорт в дубликаты.
                "fingerprint": db.make_fingerprint(
                    tg_id, item["amount"], item["kind"], item["note"], item["date"]
                ),
            }
        )

    inserted, duplicates = db.add_transactions_bulk(tg_id, prepared)

    lines = [
        f"✅ Импортировано операций: {inserted}",
        f"🔁 Пропущено дубликатов: {duplicates}",
    ]
    if truncated:
        lines.append(
            f"…файл обрезан до первых {config.MAX_IMPORT_ROWS} строк."
        )
    over = _over_budget_lines(tg_id)
    if over:
        lines.append("\nБюджеты с перерасходом:")
        lines.extend(over)
    lines.append("\nСмотри анализ: /insights")
    await _send(message, "\n".join(lines))


# --------------------------------------------------------------------------- #
# Обычные сообщения — запись трат и доходов
# --------------------------------------------------------------------------- #
@dp.message()
async def handle_transaction(message: Message):
    text = (message.text or "").strip()
    if not text:
        return

    if text.startswith("/"):
        await message.answer("Неизвестная команда. Напиши /help — список команд.")
        return

    tg_id = message.from_user.id
    db.ensure_user(tg_id)
    currency = db.get_currency(tg_id)

    is_income, cleaned = categorizers.detect_income(text)
    parsed = categorizers.parse_multiple(cleaned)
    if not parsed:
        await message.answer(
            "Не нашёл сумму в сообщении. Пиши как «300 продукты» или «такси 450»."
        )
        return

    user_keywords = db.get_user_keywords(tg_id)
    replies = []
    deltas: dict[str, float] = {}
    for amount, note in parsed:
        kind = "income" if is_income else "expense"
        category = (
            "Доход"
            if is_income
            else categorizers.guess_category(note, user_keywords)
        )
        db.add_transaction(tg_id, amount, kind, category, note)

        if is_income:
            label = f"💰 Доход: {_fmt(amount)} {currency}"
        else:
            note_txt = f" ({note})" if note else ""
            label = f"✅ {category}: {_fmt(amount)} {currency}{note_txt}"
            deltas[category] = deltas.get(category, 0) + amount
        replies.append(label)

    if deltas:
        replies.extend(_budget_warnings(tg_id, deltas))
    await _send(message, "\n".join(replies))


# --------------------------------------------------------------------------- #
# Еженедельная сводка
# --------------------------------------------------------------------------- #
async def send_due_reminders(bot: Bot):
    """Раз в минуту проверяем, кому пора отправить сводку (по его часовому поясу)."""
    for reminder in db.get_reminder_users():
        tg_id = reminder["tg_id"]
        now = datetime.now(utils.get_zone(reminder["timezone"]))
        if (
            now.weekday() != reminder["weekday"]
            or now.hour != reminder["hour"]
            or now.minute != reminder["minute"]
        ):
            continue

        today = now.strftime("%Y-%m-%d")
        if reminder["last_sent_date"] == today:
            continue
        db.mark_reminder_sent(tg_id, today)

        try:
            summary = analytics.build_ai_summary(tg_id)
            if summary["month"]["total"] == 0 and not summary["subscriptions"]:
                continue
            if not db.try_use_ai(tg_id, today, config.AI_DAILY_LIMIT):
                log.info("AI limit reached for weekly summary, user %s", tg_id)
                continue
            text = await asyncio.to_thread(ai.generate_insights, summary)
            if not text.strip():
                continue
            await _send_to(bot, tg_id, "📊 Еженедельная сводка:\n\n" + text)
        except Exception:
            log.exception("Weekly summary failed for user %s", tg_id)


@dp.errors()
async def error_handler(event: ErrorEvent):
    log.error("Unhandled error: %r", event.exception, exc_info=event.exception)
    return True


async def main():
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    config.validate()
    db.init_db()

    bot = Bot(token=config.TOKEN)
    scheduler = AsyncIOScheduler(timezone=utils.get_zone(config.DEFAULT_TIMEZONE))
    scheduler.add_job(
        send_due_reminders,
        IntervalTrigger(minutes=1),
        args=[bot],
        id="weekly_summary",
        replace_existing=True,
    )
    scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
