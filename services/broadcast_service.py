import asyncio
import logging
from datetime import datetime

from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, update

from database.models import (
    ActionType,
    AdminLog,
    Ban,
    Broadcast,
    BroadcastStatus,
    User,
)
from database.database import async_session_maker
from services.telegram_errors import is_user_unreachable, retry_after
from config import settings

logger = logging.getLogger(__name__)

# Сколько раз пробовать доставить сообщение одному пользователю.
MAX_DELIVERY_ATTEMPTS = 3

# Пауза перед повтором после непонятной ошибки (сеть, 5xx).
RETRY_DELAY_SECONDS = 2

# Запас поверх retry_after: Telegram считает время строже, чем мы.
FLOOD_EXTRA_DELAY_SECONDS = 1

# Как часто сбрасывать прогресс в базу и обновлять сообщение админу.
PROGRESS_EVERY = 50

# Сколько рассылок показывать в /ads.
DRAFT_LIST_LIMIT = 20

STATUS_EMOJI = {
    BroadcastStatus.DRAFT: "📝",
    BroadcastStatus.SENDING: "⏳",
    BroadcastStatus.SENT: "✅",
}


class BroadcastService:
    async def create_draft(self, message: Message) -> int:
        async with async_session_maker() as session:
            content_type = message.content_type
            text_content = message.text or message.caption or ""
            media_file_id = None

            if message.photo:
                media_file_id = message.photo[-1].file_id
            elif message.video:
                media_file_id = message.video.file_id

            broadcast = Broadcast(
                content=text_content,
                content_type=content_type,
                media_file_id=media_file_id,
                created_by=message.from_user.id,
                status=BroadcastStatus.DRAFT
            )
            session.add(broadcast)
            await session.commit()
            await session.refresh(broadcast)

            return broadcast.id

    async def update_draft(self, message: Message, broadcast_id: int):
        async with async_session_maker() as session:
            result = await session.execute(
                select(Broadcast).where(Broadcast.id == broadcast_id)
            )
            broadcast = result.scalar_one_or_none()

            if not broadcast:
                return

            if broadcast.status != BroadcastStatus.DRAFT:
                return

            broadcast.content = message.text or message.caption or ""

            if message.photo:
                broadcast.media_file_id = message.photo[-1].file_id
                broadcast.content_type = 'photo'
            elif message.video:
                broadcast.media_file_id = message.video.file_id
                broadcast.content_type = 'video'
            else:
                broadcast.media_file_id = None
                broadcast.content_type = 'text'

            await session.commit()

    async def list_drafts(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                select(Broadcast)
                .where(Broadcast.status != BroadcastStatus.DELETED)
                .order_by(Broadcast.id.desc())
                .limit(DRAFT_LIST_LIMIT)
            )
            broadcasts = result.scalars().all()

            if not broadcasts:
                await bot.reply_to(message, "Нет доступных рассылок.")
                return

            text = "📨 Список рассылок:\n\n"
            keyboard = InlineKeyboardMarkup()

            for bc in broadcasts:
                status_emoji = STATUS_EMOJI.get(bc.status, "•")
                preview = bc.content[:50] + "..." if len(bc.content) > 50 else bc.content
                text += f"{status_emoji} #{bc.id} - {preview}\n"

                button = InlineKeyboardButton(
                    text=f"#{bc.id} - {bc.status.value}",
                    callback_data=f"broadcast_view_{bc.id}"
                )
                keyboard.add(button)

            await bot.reply_to(message, text, reply_markup=keyboard)

    async def handle_callback(self, callback: CallbackQuery, bot: AsyncTeleBot):
        parts = callback.data.split("_")
        action = parts[1]
        broadcast_id = int(parts[2])

        if action == "view":
            await self._show_broadcast(callback, broadcast_id, bot)
        elif action == "send":
            await self._confirm_send(callback, broadcast_id, bot)
        elif action == "confirmsend":
            await self._send_broadcast(callback, broadcast_id, bot)
        elif action == "edit":
            await self._start_edit(callback, broadcast_id, bot)
        elif action == "delete":
            await self._delete_broadcast(callback, broadcast_id, bot)

    async def _show_broadcast(self, callback: CallbackQuery, broadcast_id: int, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                select(Broadcast).where(Broadcast.id == broadcast_id)
            )
            broadcast = result.scalar_one_or_none()

            if not broadcast:
                await bot.answer_callback_query(callback.id, "Рассылка не найдена.")
                return

            keyboard = InlineKeyboardMarkup()

            if broadcast.status == BroadcastStatus.DRAFT:
                keyboard.row(
                    InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"broadcast_edit_{broadcast_id}"),
                    InlineKeyboardButton(text="📤 Отправить", callback_data=f"broadcast_send_{broadcast_id}")
                )

            # Рассылку в процессе отправки удалять нельзя: цикл её уже не увидит,
            # а статистика по завершении перезапишет статус.
            if broadcast.status != BroadcastStatus.SENDING:
                keyboard.add(InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"broadcast_delete_{broadcast_id}"))

            text = f"📨 Рассылка #{broadcast_id}\n"
            text += f"Статус: {broadcast.status.value}\n"

            if broadcast.status in (BroadcastStatus.SENDING, BroadcastStatus.SENT):
                text += f"Доставлено: {broadcast.sent_count}\n"
                text += f"Не доставлено: {broadcast.failed_count}\n"

            text += "\n" + broadcast.content

            await bot.edit_message_text(text, callback.message.chat.id, callback.message.message_id, reply_markup=keyboard)

    async def _confirm_send(self, callback: CallbackQuery, broadcast_id: int, bot: AsyncTeleBot):
        recipients = await self._count_recipients()

        keyboard = InlineKeyboardMarkup()
        keyboard.row(
            InlineKeyboardButton(text="✅ Да, отправить", callback_data=f"broadcast_confirmsend_{broadcast_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"broadcast_view_{broadcast_id}")
        )

        await bot.edit_message_text(
            f"⚠️ Отправить рассылку #{broadcast_id}?\n\n"
            f"Получателей: {recipients}\n"
            f"Примерное время: {self._estimate_minutes(recipients)}\n\n"
            f"Заблокированные пользователи исключены.",
            callback.message.chat.id,
            callback.message.message_id,
            reply_markup=keyboard
        )

    def _estimate_minutes(self, recipients: int) -> str:
        seconds = recipients * settings.broadcast_delay_ms / 1000
        if seconds < 60:
            return "меньше минуты"
        return f"около {round(seconds / 60)} мин"

    async def _count_recipients(self) -> int:
        return len(await self._recipient_ids())

    async def _recipient_ids(self) -> list[int]:
        """Кому отправляем: активные и не забаненные.

        Забаненный человек не должен получать рассылки — для него бот
        закрыт, а сообщение выглядит как издёвка.
        """
        banned = select(Ban.user_id).where(Ban.is_active == True)

        async with async_session_maker() as session:
            result = await session.execute(
                select(User.user_id)
                .where(User.is_active == True)
                .where(User.user_id.not_in(banned))
                .order_by(User.user_id)
            )
            return list(result.scalars().all())

    async def _claim_broadcast(self, broadcast_id: int) -> dict | None:
        """Занять рассылку под отправку.

        Условие status == DRAFT прямо в UPDATE: если два админа нажали
        "Отправить" одновременно, второй получит rowcount == 0 и уйдёт ни с чем.
        Раньше статус менялся только в конце, и рассылка уходила дважды.
        """
        async with async_session_maker() as session:
            result = await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .where(Broadcast.status == BroadcastStatus.DRAFT)
                .values(status=BroadcastStatus.SENDING, sent_count=0, failed_count=0)
            )

            if result.rowcount != 1:
                await session.rollback()
                return None

            broadcast = (
                await session.execute(
                    select(Broadcast).where(Broadcast.id == broadcast_id)
                )
            ).scalar_one()

            snapshot = {
                "content": broadcast.content,
                "content_type": broadcast.content_type,
                "media_file_id": broadcast.media_file_id,
            }

            await session.commit()
            return snapshot

    async def _send_broadcast(self, callback: CallbackQuery, broadcast_id: int, bot: AsyncTeleBot):
        await bot.answer_callback_query(callback.id)

        snapshot = await self._claim_broadcast(broadcast_id)
        if snapshot is None:
            await bot.edit_message_text(
                "Рассылка недоступна для отправки: она уже отправлена, удалена "
                "или её прямо сейчас отправляет другой администратор.",
                callback.message.chat.id,
                callback.message.message_id
            )
            return

        recipients = await self._recipient_ids()

        await bot.edit_message_text(
            f"📤 Отправка рассылки #{broadcast_id}...\n\n0 из {len(recipients)}",
            callback.message.chat.id,
            callback.message.message_id
        )

        sent_count = 0
        failed_count = 0
        blocked_ids: list[int] = []
        delay = settings.broadcast_delay_ms / 1000

        for index, user_id in enumerate(recipients, start=1):
            outcome = await self._deliver_with_retry(bot, user_id, snapshot)

            if outcome == "sent":
                sent_count += 1
            else:
                failed_count += 1
                if outcome == "blocked":
                    blocked_ids.append(user_id)

            if index % PROGRESS_EVERY == 0:
                await self._save_progress(broadcast_id, sent_count, failed_count, blocked_ids)
                blocked_ids = []
                await self._show_progress(
                    bot, callback, broadcast_id, index, len(recipients), sent_count, failed_count
                )

            await asyncio.sleep(delay)

        await self._finish_broadcast(
            broadcast_id, callback.from_user.id, sent_count, failed_count, blocked_ids
        )

        await bot.edit_message_text(
            f"✅ Рассылка #{broadcast_id} завершена!\n\n"
            f"Отправлено: {sent_count}\n"
            f"Не доставлено: {failed_count}",
            callback.message.chat.id,
            callback.message.message_id
        )

    async def _deliver_with_retry(self, bot: AsyncTeleBot, user_id: int, snapshot: dict) -> str:
        """Доставка одному человеку. Возвращает sent, blocked или failed.

        Ключевое отличие от прежней версии: временная ошибка больше не значит,
        что пользователь потерян. Деактивируем только тех, кому Telegram прямо
        сказал, что писать некуда.
        """
        for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                await self._deliver(bot, user_id, snapshot)
                return "sent"

            except Exception as error:
                if is_user_unreachable(error):
                    return "blocked"

                wait_seconds = retry_after(error)
                if wait_seconds is not None:
                    logger.warning(
                        "Flood control: ждём %s c перед повтором для %s",
                        wait_seconds, user_id,
                    )
                    await asyncio.sleep(wait_seconds + FLOOD_EXTRA_DELAY_SECONDS)
                    continue

                if attempt < MAX_DELIVERY_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue

                logger.warning(
                    "Не доставлено пользователю %s после %s попыток: %s",
                    user_id, MAX_DELIVERY_ATTEMPTS, error,
                )
                return "failed"

        return "failed"

    async def _deliver(self, bot: AsyncTeleBot, user_id: int, snapshot: dict):
        content_type = snapshot["content_type"]
        text = snapshot["content"]
        media_file_id = snapshot["media_file_id"]

        if content_type == "photo" and media_file_id:
            await bot.send_photo(user_id, media_file_id, caption=text or None)
        elif content_type == "video" and media_file_id:
            await bot.send_video(user_id, media_file_id, caption=text or None)
        else:
            # Всё остальное уходит текстом. Молча пропускать нельзя: раньше
            # такие рассылки засчитывались как доставленные, никуда не уйдя.
            await bot.send_message(user_id, text)

    async def _save_progress(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        blocked_ids: list[int],
    ):
        """Сбросить прогресс в базу по ходу отправки.

        Раньше всё писалось одним коммитом в самом конце: падение процесса
        посреди рассылки теряло статистику целиком, а статус оставался DRAFT —
        то есть рассылку можно было запустить повторно поверх уже отправленной.
        """
        async with async_session_maker() as session:
            await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .values(sent_count=sent_count, failed_count=failed_count)
            )

            if blocked_ids:
                await session.execute(
                    update(User)
                    .where(User.user_id.in_(blocked_ids))
                    .values(is_active=False)
                )

            await session.commit()

    async def _finish_broadcast(
        self,
        broadcast_id: int,
        admin_user_id: int,
        sent_count: int,
        failed_count: int,
        blocked_ids: list[int],
    ):
        async with async_session_maker() as session:
            await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .values(
                    status=BroadcastStatus.SENT,
                    sent_at=datetime.utcnow(),
                    sent_count=sent_count,
                    failed_count=failed_count,
                )
            )

            if blocked_ids:
                await session.execute(
                    update(User)
                    .where(User.user_id.in_(blocked_ids))
                    .values(is_active=False)
                )

            session.add(AdminLog(
                admin_user_id=admin_user_id,
                action_type=ActionType.BROADCAST_SENT,
                details=f"Рассылка #{broadcast_id}: доставлено {sent_count}, не доставлено {failed_count}",
            ))

            await session.commit()

    async def _show_progress(
        self,
        bot: AsyncTeleBot,
        callback: CallbackQuery,
        broadcast_id: int,
        processed: int,
        total: int,
        sent_count: int,
        failed_count: int,
    ):
        try:
            await bot.edit_message_text(
                f"📤 Отправка рассылки #{broadcast_id}...\n\n"
                f"{processed} из {total}\n"
                f"Доставлено: {sent_count}\n"
                f"Не доставлено: {failed_count}",
                callback.message.chat.id,
                callback.message.message_id
            )
        except Exception:
            # Обновление прогресса — не повод ронять рассылку.
            pass

    async def _start_edit(self, callback: CallbackQuery, broadcast_id: int, bot: AsyncTeleBot):
        from handlers.broadcast import BroadcastStates
        await bot.set_state(callback.from_user.id, BroadcastStates.waiting_edit, callback.message.chat.id)
        async with bot.retrieve_data(callback.from_user.id, callback.message.chat.id) as data:
            data['edit_broadcast_id'] = broadcast_id
        await bot.send_message(callback.message.chat.id, "Отправьте новое содержимое для рассылки.")

    async def _delete_broadcast(self, callback: CallbackQuery, broadcast_id: int, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .where(Broadcast.status != BroadcastStatus.SENDING)
                .values(status=BroadcastStatus.DELETED)
            )
            await session.commit()

        if result.rowcount != 1:
            await bot.answer_callback_query(callback.id, "Нельзя удалить рассылку во время отправки.")
            return

        await bot.edit_message_text("🗑️ Рассылка удалена.", callback.message.chat.id, callback.message.message_id)
