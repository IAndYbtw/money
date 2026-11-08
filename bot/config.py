"""Конфигурация бота.

Все значения читаются из окружения/.env один раз при импорте.
Относительный DB_PATH разрешается относительно каталога bot/, чтобы бот
не создавал базу в случайной текущей директории.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Сначала .env рядом с кодом, затем обычный поиск (cwd и выше).
load_dotenv(BASE_DIR / ".env")
load_dotenv()


def _resolve_db_path(value: str) -> str:
    if value == ":memory:":
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)


TOKEN = os.getenv("BOT_TOKEN")

# Провайдер ИИ. Новые имена AI_* — основные, DEEPSEEK_* оставлены для
# совместимости со старыми .env. Подходит любой OpenAI-совместимый endpoint:
# DeepSeek, Gemini, Groq, OpenRouter, Ollama.
API_KEY = os.getenv("AI_API_KEY") or os.getenv("API_KEY")

AI_MODEL = os.getenv("AI_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
AI_BASE_URL = os.getenv("AI_BASE_URL") or os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
)
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT") or os.getenv("DEEPSEEK_TIMEOUT", "30"))
AI_RETRIES = int(os.getenv("AI_RETRIES") or os.getenv("DEEPSEEK_RETRIES", "2"))

# Старые имена — алиасы, чтобы не ломать существующий код и .env.
DEEPSEEK_MODEL = AI_MODEL
DEEPSEEK_BASE_URL = AI_BASE_URL
DEEPSEEK_TIMEOUT = AI_TIMEOUT
DEEPSEEK_RETRIES = AI_RETRIES

DB_PATH = _resolve_db_path(os.getenv("DB_PATH", "finbot.sqlite3"))

DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow")
DEFAULT_REMINDER_TIME = os.getenv("DEFAULT_REMINDER_TIME", "09:00")

# Сколько запросов к ИИ разрешено одному пользователю в сутки.
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "30"))

# Telegram режет сообщения длиннее 4096 символов; берём запас.
MAX_MESSAGE_LEN = 4000

# Публичный HTTPS-адрес Mini App. Если не задан — кнопка не показывается.
WEB_APP_URL = os.getenv("WEB_APP_URL") or None

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def validate() -> None:
    """Проверка настроек на старте. Без BOT_TOKEN работать нельзя.

    Без ключа ИИ бот всё равно запускается: учёт трат, бюджеты, цели и импорт
    остаются доступными, а команды ИИ отвечают понятной ошибкой.
    """
    if not TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Заполни bot/.env (см. bot/.env.example)."
        )
    if not API_KEY:
        logging.getLogger("finbot").warning(
            "API_KEY не задан — /insights, /ask и еженедельная сводка будут "
            "недоступны. Добавь AI_API_KEY в bot/.env."
        )
    if WEB_APP_URL and not WEB_APP_URL.startswith("https://"):
        raise RuntimeError("WEB_APP_URL должен начинаться с https://")
