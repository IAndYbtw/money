from aiogram import Bot, Dispatcher  # pyright: ignore[reportMissingImports]
from config import TOKEN
import asyncio
from aiogram.filters import Command  # pyright: ignore[reportMissingImports]
from aiogram.types import (  # pyright: ignore[reportMissingImports]
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

dp = Dispatcher()

WEB_APP_URL = "?????"


def open_app_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть приложение",
                    web_app=WebAppInfo(url=WEB_APP_URL)
                )
            ]
        ]
    )


@dp.message(Command("start"))
async def command_start_handler(message: Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Привет! Я помогу понять, куда уходят деньги, и найду траты, которые ты не замечаешь.\n"
        "Просто пиши мне траты в формате \"300 продукты\" или \"такси 450\" — я всё разберу сам.\n\n"
        "👇 Нажми кнопку ниже, чтобы начать 👇",
        reply_markup=open_app_keyboard()
    )


@dp.message(Command("app"))
async def command_menu_handler(message: Message):
    await message.answer(
        "Открываю приложение...",
        reply_markup=open_app_keyboard()
    )


@dp.message(Command("help"))
async def command_help_handler(message: Message):
    await message.answer(
        "ℹ️ Помощь:\n\n"
        "/app — открыть приложение\n"
        "/help — помощь\n\n"
        "Для открытия приложения нажми кнопку ниже или введи команду /app",
        reply_markup=open_app_keyboard()
    )


async def main():
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())