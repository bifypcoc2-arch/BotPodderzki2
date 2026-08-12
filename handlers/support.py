import logging

from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message

from services.support_service import (
    SupportService,
    SUPPORTED_CONTENT_TYPES,
    DELIVERED,
    BANNED,
)


logger = logging.getLogger(__name__)


BANNED_NOTICE = "⛔ Вы заблокированы в службе поддержки."
DELIVERY_ERROR = (
    "⚠️ Не удалось передать сообщение в поддержку. Попробуйте ещё раз через несколько минут."
)


def is_plain_user_message(message: Message) -> bool:
    """Личное сообщение, которое не является командой.

    Этот хендлер регистрируется первым, а telebot отдаёт сообщение первому
    подходящему. Без проверки на "/" команды /pet, /ad и /ads в личке
    никогда не доходили бы до своих хендлеров, а улетали бы в поддержку.
    """
    if message.chat.type != 'private':
        return False

    text = message.text or message.caption or ""
    return not text.startswith("/")


def register_handlers(bot: AsyncTeleBot):
    @bot.message_handler(commands=['start'])
    async def cmd_start(message: Message):
        support_service = SupportService()

        if await support_service.is_user_banned(message.from_user.id):
            await bot.reply_to(message, BANNED_NOTICE)
            return

        await bot.reply_to(
            message,
            "Добро пожаловать в службу поддержки!\n"
            "Отправьте ваше сообщение, и мы ответим в ближайшее время."
        )

    @bot.message_handler(
        func=is_plain_user_message,
        content_types=SUPPORTED_CONTENT_TYPES
    )
    async def handle_user_message(message: Message):
        support_service = SupportService()

        try:
            status = await support_service.forward_to_support(message, bot)
        except Exception as error:
            logger.exception(f"Unexpected support failure for user {message.from_user.id}: {error}")
            await bot.reply_to(message, DELIVERY_ERROR)
            return

        if status == BANNED:
            await bot.reply_to(message, BANNED_NOTICE)
            return

        if status != DELIVERED:
            await bot.reply_to(message, DELIVERY_ERROR)
            return

        await bot.reply_to(message, "Ваше сообщение отправлено в службу поддержки. Ожидайте ответа.")
