from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telebot.asyncio_handler_backends import State, StatesGroup

from services.broadcast_service import BroadcastService
from filters.role_filter import role_filter


class BroadcastStates(StatesGroup):
    waiting_content = State()
    waiting_edit = State()


def register_handlers(bot: AsyncTeleBot):
    @bot.message_handler(commands=['ad'])
    async def cmd_create_broadcast(message: Message):
        if not await role_filter(message, min_level=4):
            await bot.reply_to(message, "⛔ Недостаточно прав для выполнения команды.")
            return

        await bot.set_state(message.from_user.id, BroadcastStates.waiting_content, message.chat.id)
        await bot.reply_to(message, "Отправьте контент для рассылки (текст, фото или видео).")

    @bot.message_handler(state=BroadcastStates.waiting_content, content_types=['text', 'photo', 'video'])
    async def receive_broadcast_content(message: Message):
        broadcast_service = BroadcastService()
        draft_id = await broadcast_service.create_draft(message)
        await bot.delete_state(message.from_user.id, message.chat.id)
        await bot.reply_to(message, f"Черновик #{draft_id} создан. Используйте /ads для управления рассылками.")

    @bot.message_handler(commands=['ads'])
    async def cmd_list_broadcasts(message: Message):
        if not await role_filter(message, min_level=4):
            await bot.reply_to(message, "⛔ Недостаточно прав для выполнения команды.")
            return

        broadcast_service = BroadcastService()
        await broadcast_service.list_drafts(message, bot)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast_"))
    async def handle_broadcast_callback(call: CallbackQuery):
        broadcast_service = BroadcastService()
        await broadcast_service.handle_callback(call, bot)

    @bot.message_handler(state=BroadcastStates.waiting_edit, content_types=['text', 'photo', 'video'])
    async def receive_edit_content(message: Message):
        broadcast_service = BroadcastService()
        async with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
            broadcast_id = data.get('edit_broadcast_id')
            if broadcast_id:
                await broadcast_service.update_draft(message, broadcast_id)
                await bot.delete_state(message.from_user.id, message.chat.id)
                await bot.reply_to(message, f"✅ Рассылка #{broadcast_id} обновлена.")
