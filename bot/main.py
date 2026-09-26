from aiogram import Bot, Dispatcher
from config import TOKEN
import asyncio
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

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
        "Я помогу тебе найти, где поесть в ГУУ 🍽️\n"
        "Ты можешь посмотреть кафе и меню заведений.\n\n"
        "👇 Нажми кнопку ниже, чтобы начать",
        reply_markup=open_app_keyboard()
    )


@dp.message(Command("menu"))
async def command_menu_handler(message: Message):
    await message.answer(
        "🍔 Открываю меню заведений!",
        reply_markup=open_app_keyboard()
    )


@dp.message(Command("help"))
async def command_help_handler(message: Message):
    await message.answer(
        "ℹ️ Помощь:\n\n"
        "/menu — открыть меню и кафе\n"
        "/help — помощь\n\n"
        "Для поиска еды используй приложение 👇",
        reply_markup=open_app_keyboard()
    )


async def main():
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())