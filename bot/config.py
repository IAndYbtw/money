"""Конфигурация бота.

Все значения читаются из окружения/.env один раз при импорте.
Относительный DB_PATH разрешается относительно каталога bot/, чтобы бот
не создавал базу в случайной текущей директории.
"""

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
API_KEY = os.getenv("API_KEY")

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))
DEEPSEEK_RETRIES = int(os.getenv("DEEPSEEK_RETRIES", "2"))

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
    """Проверка обязательных настроек на старте. Падаем с понятным текстом."""
    missing = [
        name
        for name, value in (("BOT_TOKEN", TOKEN), ("API_KEY", API_KEY))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: "
            + ", ".join(missing)
            + ". Заполни bot/.env (см. bot/.env.example)."
        )
    if WEB_APP_URL and not WEB_APP_URL.startswith("https://"):
        raise RuntimeError("WEB_APP_URL должен начинаться с https://")
