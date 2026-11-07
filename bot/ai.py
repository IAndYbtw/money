"""DeepSeek API: промпты и вызовы.

Клиент создаётся лениво: импорт модуля не должен падать без API_KEY
(например, в тестах). Для запросов заданы таймаут и ретраи.
"""

import json

from openai import OpenAI

import config

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.API_KEY:
            raise RuntimeError(
                "API_KEY не задан — заполни bot/.env (см. bot/.env.example)."
            )
        _client = OpenAI(
            api_key=config.API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=config.DEEPSEEK_TIMEOUT,
            max_retries=config.DEEPSEEK_RETRIES,
        )
    return _client


SYSTEM_PROMPT = """\
Ты — финансовый ассистент в Telegram-боте. Твоя задача — анализировать
агрегированную сводку финансов пользователя (передаётся в виде JSON)
и давать конкретные, практичные наблюдения.

Правила:
1. Опирайся ТОЛЬКО на цифры из переданного JSON. Никогда не выдумывай траты,
   суммы, доходы, бюджеты или категории, которых там нет.
2. Пиши по-русски, живым разговорным языком, без канцелярита и воды.
3. Формат ответа: 2-4 коротких пункта с эмодзи, каждый — конкретное наблюдение
   + при возможности числовая оценка выгоды ("сэкономишь ~X ₽/мес").
4. Не давай общих советов в духе "тратьте меньше" — только то, что следует
   именно из этих данных.
5. Если данных мало (мало транзакций) — прямо скажи об этом и предложи
   продолжать вносить траты, не выдумывай выводы на пустом месте.
6. Если в данных есть бюджеты или цели — связывай наблюдения с ними
   (сколько осталось до лимита/цели), но не придумывай новых.
7. Не давай юридических и инвестиционных советов, только бытовую экономию.
"""


def _chat(messages: list[dict], max_tokens: int) -> str:
    response = _get_client().chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        max_tokens=max_tokens,
        messages=messages,
    )
    return _extract_text(response)


def generate_insights(summary: dict) -> str:
    """Текст для /insights на основе analytics.build_ai_summary()."""
    user_prompt = f"""\
Вот сводка финансов пользователя (JSON): траты за неделю и календарный месяц,
доходы, баланс, изменения по категориям, регулярные платежи, бюджеты и цели.

{json.dumps(summary, ensure_ascii=False, indent=2)}

Дай 3-4 самых полезных наблюдения: невидимые траты, аномальный рост,
дублирующиеся подписки, риск превышения бюджета или очевидная точка экономии.
"""
    return _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=600,
    )


def answer_question(summary: dict, question: str, history: list | None = None) -> str:
    """Свободный вопрос /ask с учётом истории диалога."""
    context_prompt = f"""\
Сводка финансов пользователя (JSON), на основе которой нужно отвечать:

{json.dumps(summary, ensure_ascii=False, indent=2)}

Вопрос пользователя: {question}

Ответь коротко (3-6 предложений), опираясь только на эти данные.
Если в данных нет информации, чтобы точно ответить — так и скажи.
"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": context_prompt})
    return _chat(messages, max_tokens=500)


def _extract_text(response) -> str:
    """Достаёт текст ответа из chat.completions-ответа DeepSeek/OpenAI SDK."""
    content = response.choices[0].message.content
    return (content or "").strip()
