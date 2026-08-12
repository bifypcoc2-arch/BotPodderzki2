from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message

from config import settings
from services.chat_moderation_service import ChatModerationService
from filters.role_filter import role_filter


NO_ACCESS = "⛔ Недостаточно прав для выполнения команды."

# Мут, размут и предупреждения — уровень старшего админа
MODERATION_LEVEL = 3
# Исключение из группы необратимо без приглашения — уровень выше
KICK_LEVEL = 4
# Смотреть статистику может любой сотрудник
VIEW_LEVEL = 1


def register_handlers(bot: AsyncTeleBot):
    """Модерация рабочей группы.

    Регистрируется до handlers.admin: там стоит обработчик всех сообщений
    группы, а telebot отдаёт сообщение первому подходящему обработчику.
    """

    service = ChatModerationService()

    async def allowed(message: Message, level: int) -> bool:
        # Команды имеют смысл только в рабочей группе: мутить и считать
        # актив в личке нечего.
        if message.chat.id != settings.forum_group_id:
            return False

        if not await role_filter(message, min_level=level):
            await bot.reply_to(message, NO_ACCESS)
            return False

        return True

    @bot.message_handler(commands=['mute'])
    async def cmd_mute(message: Message):
        if await allowed(message, MODERATION_LEVEL):
            await service.handle_mute(message, bot)

    @bot.message_handler(commands=['unmute'])
    async def cmd_unmute(message: Message):
        if await allowed(message, MODERATION_LEVEL):
            await service.handle_unmute(message, bot)

    @bot.message_handler(commands=['kick'])
    async def cmd_kick(message: Message):
        if await allowed(message, KICK_LEVEL):
            await service.handle_kick(message, bot)

    @bot.message_handler(commands=['warn'])
    async def cmd_warn(message: Message):
        if await allowed(message, MODERATION_LEVEL):
            await service.handle_warn(message, bot)

    @bot.message_handler(commands=['unwarn'])
    async def cmd_unwarn(message: Message):
        if await allowed(message, MODERATION_LEVEL):
            await service.handle_unwarn(message, bot)

    @bot.message_handler(commands=['warns'])
    async def cmd_warns(message: Message):
        if await allowed(message, VIEW_LEVEL):
            await service.send_warns(message, bot)

    @bot.message_handler(commands=['top'])
    async def cmd_top(message: Message):
        if await allowed(message, VIEW_LEVEL):
            await service.send_top(message, bot)

    @bot.message_handler(commands=['chatstat'])
    async def cmd_chatstat(message: Message):
        if await allowed(message, VIEW_LEVEL):
            await service.send_chat_stats(message, bot)
