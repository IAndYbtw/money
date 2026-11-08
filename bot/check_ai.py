"""Проверка подключения ИИ: настройки, доступность и реальный запрос.

Запуск:
    cd bot
    ./venv/bin/python check_ai.py

Работает с любым OpenAI-совместимым провайдером (DeepSeek, Gemini, Groq,
OpenRouter, Ollama) — смотрит в .env через config.
"""

import sys

import config


def _mask(value: str | None) -> str:
    if not value:
        return "НЕ ЗАДАН"
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "..." + value[-4:]


def _hint(exc: Exception) -> str:
    text = str(exc)
    code = getattr(exc, "status_code", None)
    if code == 401 or "401" in text or "Authentication" in text:
        return "Ключ неверный или отозван. Создай новый ключ у провайдера."
    if code == 402 or "402" in text or "Insufficient Balance" in text:
        return "Ключ верный, но на балансе провайдера 0. Пополни счёт или выбери бесплатный тариф."
    if code == 403 or "403" in text:
        return "Доступ запрещён: проверь, что ключ от этого провайдера и модель доступна аккаунту."
    if code == 404 or "404" in text or "model_not_found" in text:
        return "Модель или base_url не найдены. Проверь AI_MODEL и AI_BASE_URL."
    if code == 429 or "429" in text or "rate limit" in text.lower():
        return "Лимит запросов исчерпан. Подожди или выбери другую бесплатную модель."
    return "Проверь AI_BASE_URL, AI_MODEL и доступность сети."


def main() -> int:
    print("Провайдер ИИ")
    print("  AI_BASE_URL :", config.AI_BASE_URL)
    print("  AI_MODEL    :", config.AI_MODEL)
    print("  API_KEY     :", _mask(config.API_KEY))
    print("  AI_TIMEOUT  :", config.AI_TIMEOUT, "сек")
    print("  AI_RETRIES  :", config.AI_RETRIES)
    print()

    if not config.API_KEY:
        print("ОШИБКА: ключ не задан. Добавь в bot/.env: AI_API_KEY=...")
        return 1

    import ai

    try:
        client = ai._get_client()
        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[{"role": "user", "content": "Ответь одним словом: работает"}],
            max_tokens=10,
        )
    except Exception as exc:  # noqa: BLE001 - диагностика должна печатать любую ошибку
        print("ОШИБКА:", type(exc).__name__)
        print(" ", str(exc)[:500])
        print("\nЧто делать:", _hint(exc))
        return 1

    content = (response.choices[0].message.content or "").strip()
    print("OK: провайдер ответил")
    print("  ответ :", content or "(пусто)")
    print("  модель:", response.model)
    if response.usage:
        print("  токены:", response.usage.total_tokens)
    return 0


if __name__ == "__main__":
    sys.exit(main())
