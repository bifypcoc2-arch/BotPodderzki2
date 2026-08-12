import logging

from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message
from sqlalchemy import select

from config import settings
from database.models import Admin, Topic, TopicStatus, AdminLog, ActionType
from database.database import async_session_maker
from services.moderation_service import ModerationService
from services.telegram_errors import is_thread_missing, is_user_unreachable
from services.telegram_safe import try_send, OK, UNREACHABLE


logger = logging.getLogger(__name__)


# Минимальный уровень для режима SPEC. Раньше число 3 было зашито и в проверке
# команды, и в проверке доступа к теме: поменяв одно, легко было забыть второе
# и получить режим, который можно включить, но нельзя в нᄅм писать.
SPEC_LEVEL = 3


CLOSED_NOTICE = (
    "✅ Обращение закрыто.\n"
    "Если вопрос остался — просто напишите снова, мы откроем новое."
)

NOT_A_TOPIC = "Эта команда доступна только в теме обращения."
UNKNOWN_TOPIC = "Эта тема не связана ни с одним обращением."
ALREADY_CLOSED = "Обращение закрыто — сначала дождитесь нового сообщения от пользователя."

BLOCKED_NOTICE = "⚠️ Пользователь заблокировал бота — сообщение не доставлено."
DELIVERY_FAILED = (
    "⚠️ Ответ не доставлен из-за ошибки Telegram. Попробуйте отправить ещё раз."
)


class AdminService:
    async def get_admin(self, user_id: int) -> Admin | None:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Admin).where(Admin.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def set_topic_spec(self, message: Message, bot: AsyncTeleBot):
        if not message.message_thread_id:
            await bot.reply_to(message, NOT_A_TOPIC)
            return

        async with async_session_maker() as session:
            result = await session.execute(
                select(Topic).where(Topic.topic_id == message.message_thread_id)
            )
            topic = result.scalar_one_or_none()

            # Раньше бот просто молчал, и было непонятно, сработало ли.
            if not topic:
                await bot.reply_to(message, UNKNOWN_TOPIC)
                return

            # Без этой проверки /spec снимал статус CLOSED и фактически
            # переоткрывал закрытое обращение.
            if topic.status == TopicStatus.CLOSED:
                await bot.reply_to(message, ALREADY_CLOSED)
                return

            if topic.status == TopicStatus.SPEC:
                await bot.reply_to(message, "Тема уже в режиме SPEC.")
                return

            topic.status = TopicStatus.SPEC

            log_entry = AdminLog(
                admin_user_id=message.from_user.id,
                action_type=ActionType.SPEC_SET,
                topic_id=topic.id,
                details=f"User {topic.user_id}"
            )
            session.add(log_entry)

            await session.commit()

        await bot.reply_to(
            message,
            f"✅ Тема переведена в режим SPEC — отвечать могут только админы уровня {SPEC_LEVEL} и выше."
        )

    async def unset_topic_spec(self, message: Message, bot: AsyncTeleBot):
        if not message.message_thread_id:
            await bot.reply_to(message, NOT_A_TOPIC)
            return

        async with async_session_maker() as session:
            result = await session.execute(
                select(Topic).where(Topic.topic_id == message.message_thread_id)
            )
            topic = result.scalar_one_or_none()

            if not topic:
                await bot.reply_to(message, UNKNOWN_TOPIC)
                return

            if topic.status == TopicStatus.CLOSED:
                await bot.reply_to(message, ALREADY_CLOSED)
                return

            if topic.status != TopicStatus.SPEC:
                await bot.reply_to(message, "Тема и так не в режиме SPEC.")
                return

            # Раньше всегда ставился CLAIMED, и тема числилась "в работе"
            # без единого ответа и без исполнителя в claimed_by.
            topic.status = (
                TopicStatus.CLAIMED if topic.claimed_by else TopicStatus.OPEN
            )

            log_entry = AdminLog(
                admin_user_id=message.from_user.id,
                action_type=ActionType.SPEC_UNSET,
                topic_id=topic.id,
                details=f"User {topic.user_id}"
            )
            session.add(log_entry)

            await session.commit()

        await bot.reply_to(message, "✅ Режим SPEC снят")

    async def close_topic(self, message: Message, bot: AsyncTeleBot):
        """Закрывает обращение.

        После этого следующее сообщение пользователя создаст новую тему:
        _get_or_create_topic ищет только темы со статусом не CLOSED.
        """
        if not message.message_thread_id:
            await bot.reply_to(message, NOT_A_TOPIC)
            return

        async with async_session_maker() as session:
            result = await session.execute(
                select(Topic).where(Topic.topic_id == message.message_thread_id)
            )
            topic = result.scalar_one_or_none()

            if not topic:
                await bot.reply_to(message, UNKNOWN_TOPIC)
                return

            if topic.status == TopicStatus.CLOSED:
                await bot.reply_to(message, "Обращение уже закрыто.")
                return

            user_id = topic.user_id
            topic.status = TopicStatus.CLOSED

            log_entry = AdminLog(
                admin_user_id=message.from_user.id,
                action_type=ActionType.TOPIC_CLOSED,
                topic_id=topic.id,
                details=f"User {user_id}"
            )
            session.add(log_entry)

            await session.commit()

        # Уведомление пользователя не отменяет закрытие: если он заблокировал бота,
        # тема всё равно должна закрыться.
        status, payload = await try_send(bot.send_message, user_id, CLOSED_NOTICE)
        if status == UNREACHABLE:
            logger.info(f"User {user_id} is unreachable, close notice skipped")
        elif status != OK:
            logger.error(f"Failed to notify user {user_id} about close: {payload}")

        # Сворачиваем тему в Telegram, чтобы она не висела в активных.
        # Удалять нельзя: переписка нужна для разборов.
        try:
            await bot.close_forum_topic(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id
            )
        except Exception as error:
            if not is_thread_missing(error):
                logger.error(f"Failed to close forum topic {message.message_thread_id}: {error}")

            await bot.send_message(
                message.chat.id,
                "✅ Обращение закрыто.",
                message_thread_id=message.message_thread_id
            )

    async def handle_admin_message(self, message: Message, bot: AsyncTeleBot):
        if not message.message_thread_id:
            return

        if message.forward_from or message.forward_from_chat:
            return

        # Этот хендлер регистрируется раньше хендлеров /ad, /ads и /pet,
        # а telebot отдаёт сообщение первому подходящему. Без этой проверки
        # команда, набранная внутри темы, ушла бы пользователю как обычный ответ.
        text = message.text or message.caption or ""
        if text.startswith("/"):
            return

        async with async_session_maker() as session:
            result = await session.execute(
                select(Topic).where(Topic.topic_id == message.message_thread_id)
            )
            topic = result.scalar_one_or_none()

            if not topic:
                return

            if topic.status == TopicStatus.CLOSED:
                # Раньше такой ответ всё равно уходил пользователю — уже после того,
                # как тому сообщили о закрытии, и при этом его ответ попадал уже в новую тему.
                await bot.send_message(
                    message.chat.id,
                    "⚠️ Обращение закрыто, сообщение не отправлено. Ждите нового обращения от пользователя.",
                    message_thread_id=message.message_thread_id
                )
                return

            if topic.status == TopicStatus.SPEC:
                admin = await self.get_admin(message.from_user.id)

                if not admin or admin.role_level < SPEC_LEVEL:
                    # Удаление может быть недоступно: у бота нет права удалять
                    # сообщения или сообщение старше двух суток. Раньше в этом случае
                    # падало исключение и предупреждение в тему так и не появлялось.
                    try:
                        await bot.delete_message(message.chat.id, message.message_id)
                    except Exception as error:
                        logger.warning(f"Failed to delete message in SPEC topic: {error}")

                    await bot.send_message(
                        message.chat.id,
                        "⛔ Доступ к этой теме ограничен.",
                        message_thread_id=message.message_thread_id
                    )
                    return

            # Главный путь всего бота: здесь ответ сотрудника уходит клиенту.
            # Лимит Telegram или моргнувшая сеть больше не стоят ответа:
            # try_send сам подождёт столько, сколько сказал Telegram, и повторит.
            status, payload = await try_send(
                bot.copy_message,
                chat_id=topic.user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

            if status != OK:
                logger.error(f"Failed to deliver reply to user {topic.user_id}: {payload}")

                if status == UNREACHABLE:
                    await bot.send_message(
                        message.chat.id,
                        BLOCKED_NOTICE,
                        message_thread_id=message.message_thread_id
                    )
                else:
                    # Админ должен знать, что ответ не ушёл, иначе он считает
                    # обращение отработанным.
                    await bot.send_message(
                        message.chat.id,
                        DELIVERY_FAILED,
                        message_thread_id=message.message_thread_id
                    )

                return

            # Отмечаем, кто взял обращение. Колонка claimed_by была в модели,
            # но никто её не заполнял, и статус CLAIMED никогда не выставлялся.
            if topic.claimed_by is None:
                topic.claimed_by = message.from_user.id

                if topic.status == TopicStatus.OPEN:
                    topic.status = TopicStatus.CLAIMED

                session.add(AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.TOPIC_CLAIMED,
                    topic_id=topic.id,
                    details=f"User {topic.user_id}"
                ))

                await session.commit()

        moderation_service = ModerationService()
        await moderation_service.increment_admin_messages(message.from_user.id)
