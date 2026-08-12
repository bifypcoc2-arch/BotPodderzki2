import asyncio
import logging

from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User, Topic, TopicStatus, Stats
from database.database import async_session_maker
from services.anonymity import anonymous_code
from services.moderation_service import ModerationService
from services.telegram_errors import is_thread_missing
from config import settings


logger = logging.getLogger(__name__)

__all__ = [
    "SupportService",
    "anonymous_code",
    "SUPPORTED_CONTENT_TYPES",
    "DELIVERED",
    "BANNED",
    "FAILED",
]


# Раньше здесь было только text/photo/video/document, поэтому голосовое,
# кружок, стикер, гифка и аудио не доходили до поддержки вообще: хендлер
# их не ловил, и пользователь оставался без ответа, не понимая почему.
SUPPORTED_CONTENT_TYPES = [
    'text',
    'photo',
    'video',
    'document',
    'audio',
    'voice',
    'video_note',
    'sticker',
    'animation',
]


# Статусы доставки. Раньше метод возвращал bool, и "не доставлено из-за бана"
# было не отличить от "не доставлено из-за ошибки Telegram".
DELIVERED = "delivered"
BANNED = "banned"
FAILED = "failed"


class SupportService:
    # Локи общие для всех экземпляров: сервис создаётся заново на каждое
    # сообщение, поэтому хранить их на экземпляре бессмысленно.
    _topic_locks: dict[int, asyncio.Lock] = {}
    _locks_guard = asyncio.Lock()

    async def is_user_banned(self, user_id: int) -> bool:
        moderation_service = ModerationService()
        return await moderation_service.is_banned(user_id)

    async def forward_to_support(self, message: Message, bot: AsyncTeleBot) -> str:
        """Передаёт сообщение в тему поддержки.

        Возвращает DELIVERED, BANNED или FAILED.
        """
        user_id = message.from_user.id

        if await self.is_user_banned(user_id):
            return BANNED

        async with async_session_maker() as session:
            await self._get_or_create_user(session, message.from_user)
            await session.commit()

        try:
            topic_id = await self._resolve_topic(user_id, bot)
        except Exception as error:
            logger.error(f"Failed to open topic for user {user_id}: {error}")
            return FAILED

        async with async_session_maker() as session:
            await self._increment_message_count(session, user_id)

        try:
            await self._copy_to_topic(message, bot, topic_id)
        except Exception as error:
            if not is_thread_missing(error):
                logger.error(f"Failed to copy message to topic {topic_id}: {error}")
                return FAILED

            # Тему удалили в Telegram руками, а в базе она числится открытой.
            # Без этой ветки пользователь навсегда терял связь с поддержкой:
            # каждое его сообщение падало с той же ошибкой.
            logger.warning(f"Topic {topic_id} is gone, recreating for user {user_id}")

            try:
                async with async_session_maker() as session:
                    await self._mark_topic_closed(session, topic_id)

                new_topic_id = await self._resolve_topic(user_id, bot)
                await self._copy_to_topic(message, bot, new_topic_id)
            except Exception as retry_error:
                logger.error(f"Failed to recreate topic for user {user_id}: {retry_error}")
                return FAILED

        return DELIVERED

    async def _resolve_topic(self, user_id: int, bot: AsyncTeleBot) -> int:
        """Находит открытую тему пользователя или создаёт новую.

        Под локом на пользователя: два сообщения, отправленные подряд,
        обрабатываются параллельно, и без лока оба не находили темы и оба
        создавали свою — в форуме появлялись две темы на одного человека.
        """
        lock = await self._lock_for(user_id)

        async with lock:
            async with async_session_maker() as session:
                return await self._get_or_create_topic(session, user_id, bot)

    async def _lock_for(self, user_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._topic_locks.get(user_id)

            if lock is None:
                lock = asyncio.Lock()
                self._topic_locks[user_id] = lock

            return lock

    async def _copy_to_topic(self, message: Message, bot: AsyncTeleBot, topic_id: int):
        # copy_message, а не forward_message: копия не содержит ссылки на автора,
        # поэтому в топике личность пользователя не раскрывается.
        await bot.copy_message(
            chat_id=settings.forum_group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id
        )

    async def _mark_topic_closed(self, session: AsyncSession, topic_id: int):
        result = await session.execute(
            select(Topic).where(Topic.topic_id == topic_id)
        )
        topic = result.scalars().first()

        if topic:
            topic.status = TopicStatus.CLOSED
            await session.commit()

    async def _get_or_create_user(self, session: AsyncSession, from_user) -> User:
        result = await session.execute(
            select(User).where(User.user_id == from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                user_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name
            )
            session.add(user)
            return user

        # Человек мог сменить ник или имя. Раньше в базе навсегда оставались
        # значения первого дня, и владелец в /stats видел устаревший username.
        if user.username != from_user.username:
            user.username = from_user.username

        if user.first_name != from_user.first_name:
            user.first_name = from_user.first_name

        return user

    async def _get_or_create_topic(self, session: AsyncSession, user_id: int, bot: AsyncTeleBot) -> int:
        result = await session.execute(
            select(Topic).where(
                Topic.user_id == user_id,
                Topic.status != TopicStatus.CLOSED
            ).order_by(Topic.id.desc())
        )
        topic = result.scalars().first()

        if topic:
            return topic.topic_id

        # В названии темы только анонимный код — ни имени, ни username, ни ID.
        topic_name = f"Обращение #{anonymous_code(user_id)}"
        created_topic = await bot.create_forum_topic(
            chat_id=settings.forum_group_id,
            name=topic_name
        )

        topic = Topic(
            user_id=user_id,
            topic_id=created_topic.message_thread_id,
            status=TopicStatus.OPEN
        )
        session.add(topic)
        # Коммитим сразу: иначе при ошибке отправки тема останется в Telegram,
        # но не в БД, и на следующем сообщении создастся дубль.
        await session.commit()

        return topic.topic_id

    async def _increment_message_count(self, session: AsyncSession, user_id: int):
        """Счётчик обращений пользователя для /stats."""
        result = await session.execute(
            select(Stats).where(Stats.user_id == user_id)
        )
        stats = result.scalars().first()

        if not stats:
            stats = Stats(user_id=user_id, messages_sent=0)
            session.add(stats)

        stats.messages_sent = (stats.messages_sent or 0) + 1
        await session.commit()
