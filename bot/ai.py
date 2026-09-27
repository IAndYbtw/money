import json
from openai import OpenAI  # type: ignore[import-not-found]

from config import API_KEY, DEEPSEEK_MODEL

# DeepSeek API полностью совместим с OpenAI SDK, меняется только base_url
client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

# Системный промпт задаёт роль и жёсткие рамки: не выдумывать цифры,
# опираться только на переданный JSON, писать по-человечески и коротко.
SYSTEM_PROMPT = """\
Ты — финансовый ассистент в Telegram-боте. Твоя задача — анализировать агрегированную
сводку трат пользователя (передаётся в виде JSON) и давать конкретные, практичные
наблюдения и рекомендации.

Правила:
1. Опирайся ТОЛЬКО на цифры из переданного JSON. Никогда не выдумывай траты,
   суммы или категории, которых там нет.
2. Пиши по-русски, живым разговорным языком, без канцелярита и воды.
3. Формат ответа: 2-4 коротких пункта с эмодзи, каждый — конкретное наблюдение
   + при возможности числовая оценка выгоды ("сэкономишь ~X ₽/мес").
4. Не давай общих советов в духе "тратьте меньше" — только то, что следует
   именно из этих данных.
5. Если данных мало (мало транзакций) — прямо скажи об этом и предложи
   продолжать вносить траты, не выдумывай выводы на пустом месте.
6. Не давай юридических и инвестиционных советов, только бытовую экономию.
"""


def generate_insights(summary: dict) -> str:
    """
    Генерирует текст /insights на основе агрегированной сводки.
    summary — результат analytics.build_ai_summary()
    """
    user_prompt = f"""\
Вот сводка трат пользователя за неделю и месяц, изменения по категориям
и обнаруженные регулярные платежи (в формате JSON):

{json.dumps(summary, ensure_ascii=False, indent=2)}

Дай 3-4 самых полезных наблюдения: где есть невидимые траты, аномальный рост,
дублирующиеся подписки или очевидная точка экономии.
"""
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        max_tokens=600,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return _extract_text(response)


def answer_question(summary: dict, question: str, history: list = None) -> str:
    """
    Обрабатывает команду /ask — свободный вопрос пользователя о своих финансах.
    history — список предыдущих сообщений [{"role": "user"/"assistant", "content": "..."}]
    для поддержания контекста диалога (опционально).
    """
    context_prompt = f"""\
Сводка трат пользователя (JSON), на основе которой нужно отвечать:

{json.dumps(summary, ensure_ascii=False, indent=2)}

Вопрос пользователя: {question}

Ответь коротко (3-6 предложений), опираясь только на эти данные.
Если в данных нет информации, чтобы точно ответить — так и скажи.
"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": context_prompt})

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        max_tokens=500,
        messages=messages,
    )
    return _extract_text(response)


def _extract_text(response) -> str:
    """Достаёт текст ответа из chat.completions-ответа DeepSeek/OpenAI SDK."""
    return response.choices[0].message.content.strip()
