from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message

from config import settings
from database.models import OWNER_LEVEL
from services.admin_service import AdminService, SPEC_LEVEL
from services.chat_moderation_service import ChatModerationService
from services.moderation_service import ModerationService
from services.support_service import SUPPORTED_CONTENT_TYPES
from filters.role_filter import role_filter


NO_ACCESS = "⛔ Недостаточно прав для выполнения команды."


def register_handlers(bot: AsyncTeleBot):
    @bot.message_handler(commands=['spec'])
    async def cmd_spec(message: Message):
        if not await role_filter(message, min_level=SPEC_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        admin_service = AdminService()
        await admin_service.set_topic_spec(message, bot)

    @bot.message_handler(commands=['unspec'])
    async def cmd_unspec(message: Message):
        if not await role_filter(message, min_level=SPEC_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        admin_service = AdminService()
        await admin_service.unset_topic_spec(message, bot)

    @bot.message_handler(commands=['close'])
    async def cmd_close(message: Message):
        # Закрыть обращение может любой админ: это рутинное действие,
        # а не привилегия. Закрытие обратимо — пользователь просто напишет снова.
        if not await role_filter(message, min_level=1):
            await bot.reply_to(message, NO_ACCESS)
            return

        admin_service = AdminService()
        await admin_service.close_topic(message, bot)

    @bot.message_handler(commands=['ban'])
    async def cmd_ban(message: Message):
        if not await role_filter(message, min_level=OWNER_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        moderation_service = ModerationService()
        await moderation_service.handle_ban_command(message, bot)

    @bot.message_handler(commands=['unban'])
    async def cmd_unban(message: Message):
        if not await role_filter(message, min_level=OWNER_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        moderation_service = ModerationService()
        await moderation_service.handle_unban_command(message, bot)

    @bot.message_handler(commands=['bans'])
    async def cmd_bans(message: Message):
        if not await role_filter(message, min_level=OWNER_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        moderation_service = ModerationService()
        await moderation_service.send_ban_list(message, bot)

    @bot.message_handler(commands=['stats'])
    async def cmd_stats(message: Message):
        if not await role_filter(message, min_level=OWNER_LEVEL):
            await bot.reply_to(message, NO_ACCESS)
            return

        moderation_service = ModerationService()
        await moderation_service.send_message_stats(message, bot)

    @bot.message_handler(
        func=lambda m: m.chat.id == settings.forum_group_id,
        content_types=SUPPORTED_CONTENT_TYPES
    )
    async def handle_admin_reply(message: Message):
        # Считаем актив здесь, а не в middleware: этот обработчик и так видит
        # все сообщения группы — и в темах обращений, и в общем чате.
        chat_moderation = ChatModerationService()
        await chat_moderation.record_message(message)

        admin_service = AdminService()
        await admin_service.handle_admin_message(message, bot)
