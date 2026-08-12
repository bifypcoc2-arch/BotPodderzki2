from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import settings


def register_handlers(bot: AsyncTeleBot):
    @bot.message_handler(commands=['pet'])
    async def cmd_open_pet(message: Message):
        markup = InlineKeyboardMarkup()
        webapp = WebAppInfo(url=settings.mini_app_url)
        button = InlineKeyboardButton(text="🐾 Открыть питомца", web_app=webapp)
        markup.add(button)

        await bot.reply_to(message, "Откройте мини-приложение для ухода за питомцем:", reply_markup=markup)
