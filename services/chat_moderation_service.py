import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from telebot.async_telebot import AsyncTeleBot
from telebot.types import ChatPermissions, Message
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import async_session_maker
from database.models import ActionType, Admin, AdminLog, ChatActivity, Warn


logger = logging.getLogger(__name__)


# Сколько предупреждений до автоматического мута
WARN_LIMIT = 3
WARN_MUTE_SECONDS = 24 * 60 * 60

DEFAULT_MUTE_SECONDS = 60 * 60
# Telegram считает ограничение меньше 30 секунд или больше 366 дней вечным
MIN_MUTE_SECONDS = 60
MAX_MUTE_SECONDS = 365 * 24 * 60 * 60

TOP_LIMIT = 10
WARN_LIST_LIMIT = 20

_DURATION_UNITS = {
    "s": 1, "с": 1,
    "m": 60, "м": 60,
    "h": 3600, "ч": 3600,
    "d": 86400, "д": 86400,
    "w": 604800, "н": 604800,
}


def parse_duration(token: str) -> Optional[int]:
    """'30m', '2ч', '1d', '45' -> секунды. Голое число — это минуты."""
    token = (token or "").strip().lower()

    if not token:
        return None

    if token.isdigit():
        return int(token) * 60

    unit = _DURATION_UNITS.get(token[-1])
    value = token[:-1]

    if unit is None or not value.isdigit():
        return None

    return int(value) * unit


def format_duration(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60

    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")

    return " ".join(parts) if parts else f"{seconds} сек"


def _muted_permissions() -> ChatPermissions:
    return ChatPermissions(can_send_messages=False)


def _open_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


class ChatModerationService:
    """Модерация рабочей группы и учёт актива.

    Касается только сотрудников внутри группы. За доступ обратившихся
    к поддержке отвечает ModerationService с его /ban.
    Проверка уровня доступа живёт в хендлерах.
    """

    # ---------------------------------------------------------------- актив

    async def record_message(self, message: Message):
        """Считает сообщение участника группы.

        Ответы, скопированные в тему самим ботом, сюда не попадают:
        бот не получает апдейты о собственных сообщениях.
        """
        user = message.from_user

        if user is None or user.is_bot:
            return

        try:
            async with async_session_maker() as session:
                row = await self._activity(session, message.chat.id, user.id)

                if row is None:
                    row = ChatActivity(
                        chat_id=message.chat.id,
                        user_id=user.id,
                        messages_count=0,
                    )
                    session.add(row)

                row.username = user.username
                row.display_name = user.first_name
                row.messages_count = (row.messages_count or 0) + 1
                row.last_message_at = datetime.utcnow()

                await session.commit()
        except Exception as error:
            # Счётчик — вещь второстепенная: его падение не должно мешать
            # доставке ответа пользователю.
            logger.error(f"Failed to record activity for {user.id}: {error}")

    async def send_top(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                select(ChatActivity)
                .where(
                    ChatActivity.chat_id == message.chat.id,
                    ChatActivity.messages_count > 0,
                )
                .order_by(desc(ChatActivity.messages_count))
                .limit(TOP_LIMIT)
            )
            rows = result.scalars().all()

        if not rows:
            await bot.reply_to(message, "Пока нечего показать — сообщений в группе ещё не было.")
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = ["🏆 Топ актива", ""]

        for index, row in enumerate(rows, start=1):
            prefix = medals.get(index, f"{index}.")
            label = self._label(row.user_id, row.username, row.display_name)
            lines.append(f"{prefix} {label} — {row.messages_count}")

        await bot.reply_to(message, "\n".join(lines))

    async def send_chat_stats(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            totals = await session.execute(
                select(
                    func.count(ChatActivity.id),
                    func.coalesce(func.sum(ChatActivity.messages_count), 0),
                ).where(ChatActivity.chat_id == message.chat.id)
            )
            members, total_messages = totals.one()

            warns_result = await session.execute(
                select(func.count(Warn.id)).where(
                    Warn.chat_id == message.chat.id,
                    Warn.is_active.is_(True),
                )
            )
            active_warns = warns_result.scalar() or 0

            day_ago = datetime.utcnow() - timedelta(days=1)
            recent_result = await session.execute(
                select(func.count(ChatActivity.id)).where(
                    ChatActivity.chat_id == message.chat.id,
                    ChatActivity.last_message_at >= day_ago,
                )
            )
            recent_members = recent_result.scalar() or 0

        lines = [
            "📈 Статистика группы",
            "",
            f"Всего сообщений: {total_messages}",
            f"Участников в учёте: {members}",
            f"Писали за сутки: {recent_members}",
            f"Активных предупреждений: {active_warns}",
        ]

        await bot.reply_to(message, "\n".join(lines))

    # ----------------------------------------------------------- модерация

    async def handle_mute(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            target_id, label, seconds, reason, error = await self._parse_command(
                session, message, expect_duration=True
            )

            if error:
                await bot.reply_to(message, error)
                return

            denied = await self._check_hierarchy(session, message.from_user.id, target_id)
            if denied:
                await bot.reply_to(message, denied)
                return

            seconds = seconds or DEFAULT_MUTE_SECONDS
            seconds = max(MIN_MUTE_SECONDS, min(seconds, MAX_MUTE_SECONDS))

            if not await self._apply_mute(message.chat.id, target_id, seconds, bot):
                await bot.reply_to(
                    message,
                    "Не получилось ограничить. Проверьте, что у бота есть право блокировать участников и что человек не является администратором чата в Telegram.",
                )
                return

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.MEMBER_MUTED,
                    details=f"{label} на {format_duration(seconds)}"
                    + (f": {reason}" if reason else ""),
                )
            )
            await session.commit()

        text = f"🔇 {label} не может писать {format_duration(seconds)}."
        if reason:
            text += f"\nПричина: {reason}"

        await bot.reply_to(message, text)

    async def handle_unmute(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            target_id, label, _, _, error = await self._parse_command(
                session, message, expect_duration=False
            )

            if error:
                await bot.reply_to(message, error)
                return

            try:
                await bot.restrict_chat_member(
                    chat_id=message.chat.id,
                    user_id=target_id,
                    permissions=_open_permissions(),
                )
            except Exception as unmute_error:
                logger.error(f"Failed to unmute {target_id}: {unmute_error}")
                await bot.reply_to(message, "Не получилось снять ограничение.")
                return

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.MEMBER_UNMUTED,
                    details=label,
                )
            )
            await session.commit()

        await bot.reply_to(message, f"🔈 {label} снова может писать.")

    async def handle_kick(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            target_id, label, _, reason, error = await self._parse_command(
                session, message, expect_duration=False
            )

            if error:
                await bot.reply_to(message, error)
                return

            denied = await self._check_hierarchy(session, message.from_user.id, target_id)
            if denied:
                await bot.reply_to(message, denied)
                return

            try:
                # Бан и сразу разбан — это и есть кик: человек выходит из группы,
                # но может вернуться по приглашению.
                await bot.ban_chat_member(message.chat.id, target_id)
                await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
            except Exception as kick_error:
                logger.error(f"Failed to kick {target_id}: {kick_error}")
                await bot.reply_to(
                    message,
                    "Не получилось исключить. Проверьте права бота в группе.",
                )
                return

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.MEMBER_KICKED,
                    details=label + (f": {reason}" if reason else ""),
                )
            )
            await session.commit()

        text = f"👢 {label} исключён из группы."
        if reason:
            text += f"\nПричина: {reason}"

        await bot.reply_to(message, text)

    async def handle_warn(self, message: Message, bot: AsyncTeleBot):
        auto_muted = False

        async with async_session_maker() as session:
            target_id, label, _, reason, error = await self._parse_command(
                session, message, expect_duration=False
            )

            if error:
                await bot.reply_to(message, error)
                return

            denied = await self._check_hierarchy(session, message.from_user.id, target_id)
            if denied:
                await bot.reply_to(message, denied)
                return

            session.add(
                Warn(
                    chat_id=message.chat.id,
                    user_id=target_id,
                    issued_by=message.from_user.id,
                    reason=reason,
                    is_active=True,
                )
            )
            await session.flush()

            count = await self._active_warns(session, message.chat.id, target_id)

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.MEMBER_WARNED,
                    details=f"{label} ({count}/{WARN_LIMIT})"
                    + (f": {reason}" if reason else ""),
                )
            )

            if count >= WARN_LIMIT:
                auto_muted = await self._apply_mute(
                    message.chat.id, target_id, WARN_MUTE_SECONDS, bot
                )

                if auto_muted:
                    # Предупреждения сгорают вместе с наказанием, иначе каждое
                    # следующее сразу давало бы новый суточный мут.
                    await self._clear_warns(
                        session, message.chat.id, target_id, message.from_user.id
                    )

                    session.add(
                        AdminLog(
                            admin_user_id=message.from_user.id,
                            action_type=ActionType.MEMBER_MUTED,
                            details=f"{label} автоматически на {format_duration(WARN_MUTE_SECONDS)}",
                        )
                    )

            await session.commit()

        text = f"⚠️ Предупреждение {label}: {min(count, WARN_LIMIT)}/{WARN_LIMIT}"
        if reason:
            text += f"\nПричина: {reason}"

        if count >= WARN_LIMIT:
            if auto_muted:
                text += (
                    f"\n\n🔇 Лимит исчерпан — молчание на {format_duration(WARN_MUTE_SECONDS)}, "
                    "счётчик обнулён."
                )
            else:
                text += "\n\n⚠️ Лимит исчерпан, но ограничить не вышло — проверьте права бота."

        await bot.reply_to(message, text)

    async def handle_unwarn(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            target_id, label, _, _, error = await self._parse_command(
                session, message, expect_duration=False
            )

            if error:
                await bot.reply_to(message, error)
                return

            result = await session.execute(
                select(Warn)
                .where(
                    Warn.chat_id == message.chat.id,
                    Warn.user_id == target_id,
                    Warn.is_active.is_(True),
                )
                .order_by(desc(Warn.created_at))
                .limit(1)
            )
            warn = result.scalars().first()

            if warn is None:
                await bot.reply_to(message, f"У {label} нет активных предупреждений.")
                return

            warn.is_active = False
            warn.removed_by = message.from_user.id
            warn.removed_at = datetime.utcnow()

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.WARN_REMOVED,
                    details=label,
                )
            )

            await session.flush()
            count = await self._active_warns(session, message.chat.id, target_id)
            await session.commit()

        await bot.reply_to(
            message, f"✅ Предупреждение снято. У {label} осталось {count}/{WARN_LIMIT}."
        )

    async def send_warns(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                select(Warn.user_id, func.count(Warn.id).label("total"))
                .where(Warn.chat_id == message.chat.id, Warn.is_active.is_(True))
                .group_by(Warn.user_id)
                .order_by(desc("total"))
                .limit(WARN_LIST_LIMIT)
            )
            rows = result.all()

            if not rows:
                await bot.reply_to(message, "Активных предупреждений нет.")
                return

            lines = ["⚠️ Активные предупреждения", ""]

            for user_id, total in rows:
                activity = await self._activity(session, message.chat.id, user_id)
                label = self._label(
                    user_id,
                    activity.username if activity else None,
                    activity.display_name if activity else None,
                )
                lines.append(f"{label} — {total}/{WARN_LIMIT}")

        await bot.reply_to(message, "\n".join(lines))

    # -------------------------------------------------------------- внутреннее

    async def _apply_mute(
        self, chat_id: int, user_id: int, seconds: int, bot: AsyncTeleBot
    ) -> bool:
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=_muted_permissions(),
                until_date=int(until.timestamp()),
            )
            return True
        except Exception as error:
            logger.error(f"Failed to mute {user_id} in {chat_id}: {error}")
            return False

    async def _activity(
        self, session: AsyncSession, chat_id: int, user_id: int
    ) -> Optional[ChatActivity]:
        result = await session.execute(
            select(ChatActivity).where(
                ChatActivity.chat_id == chat_id,
                ChatActivity.user_id == user_id,
            )
        )
        return result.scalars().first()

    async def _active_warns(self, session: AsyncSession, chat_id: int, user_id: int) -> int:
        result = await session.execute(
            select(func.count(Warn.id)).where(
                Warn.chat_id == chat_id,
                Warn.user_id == user_id,
                Warn.is_active.is_(True),
            )
        )
        return result.scalar() or 0

    async def _clear_warns(
        self, session: AsyncSession, chat_id: int, user_id: int, actor_id: int
    ):
        result = await session.execute(
            select(Warn).where(
                Warn.chat_id == chat_id,
                Warn.user_id == user_id,
                Warn.is_active.is_(True),
            )
        )

        for warn in result.scalars().all():
            warn.is_active = False
            warn.removed_by = actor_id
            warn.removed_at = datetime.utcnow()

    def _label(
        self, user_id: int, username: Optional[str], display_name: Optional[str]
    ) -> str:
        if username:
            return f"@{username}"
        if display_name:
            return f"{display_name} ({user_id})"
        return str(user_id)

    def _replied(self, message: Message) -> Optional[Message]:
        """Ответ на человека, а не на служебное сообщение темы.

        В форуме первое сообщение темы Telegram отдаёт как ответ на событие
        о создании темы. Без этой проверки команда без аргументов внутри темы
        выглядела бы как ответ на кого-то.
        """
        replied = message.reply_to_message

        if replied is None:
            return None

        if getattr(replied, "forum_topic_created", None) is not None:
            return None

        return replied

    async def _resolve_token(
        self, session: AsyncSession, chat_id: int, token: str
    ) -> Optional[int]:
        token = token.strip()

        if token.lstrip("-").isdigit():
            return int(token)

        username = token.lstrip("@").lower()

        if not username:
            return None

        result = await session.execute(
            select(ChatActivity).where(
                ChatActivity.chat_id == chat_id,
                func.lower(ChatActivity.username) == username,
            )
        )
        row = result.scalars().first()

        return row.user_id if row else None

    async def _parse_command(
        self, session: AsyncSession, message: Message, expect_duration: bool
    ):
        """Разбирает '/mute @user 30m спам' и '/mute 30m спам' в ответе на сообщение.

        Возвращает (user_id, подпись, секунды, причина, ошибка).
        """
        parts = (message.text or "").split()[1:]
        replied = self._replied(message)

        target_token = None
        if parts and replied is None:
            first = parts[0]
            if first.startswith("@") or first.lstrip("-").isdigit():
                target_token = parts.pop(0)

        seconds = None
        if expect_duration and parts:
            parsed = parse_duration(parts[0])
            if parsed:
                seconds = parsed
                parts.pop(0)

        reason = " ".join(parts).strip() or None

        if replied is not None:
            author = replied.from_user

            if author is None:
                return None, None, seconds, reason, "Не вижу, кто автор сообщения."

            if author.is_bot:
                return (
                    None,
                    None,
                    seconds,
                    reason,
                    "Это сообщение отправлено ботом. Чтобы закрыть доступ обратившемуся, используйте /ban в его теме.",
                )

            label = self._label(author.id, author.username, author.first_name)
            return author.id, label, seconds, reason, None

        if target_token is None:
            return (
                None,
                None,
                seconds,
                reason,
                "Не понял, к кому применить. Ответьте на сообщение человека или укажите @username или его ID.",
            )

        target_id = await self._resolve_token(session, message.chat.id, target_token)

        if target_id is None:
            return (
                None,
                None,
                seconds,
                reason,
                f"Не нашёл {target_token} в этой группе. По юзернейму человек находится только после того, как напишет хотя бы раз — ответьте на его сообщение или укажите числовой ID.",
            )

        activity = await self._activity(session, message.chat.id, target_id)
        label = self._label(
            target_id,
            activity.username if activity else None,
            activity.display_name if activity else None,
        )

        return target_id, label, seconds, reason, None

    async def _check_hierarchy(
        self, session: AsyncSession, actor_id: int, target_id: int
    ) -> Optional[str]:
        """Не даёт модерировать себя и равных или старших по уровню."""
        if actor_id == target_id:
            return "Эта команда не применяется к себе."

        target_result = await session.execute(
            select(Admin).where(Admin.user_id == target_id)
        )
        target_admin = target_result.scalars().first()

        if target_admin is None:
            return None

        actor_result = await session.execute(select(Admin).where(Admin.user_id == actor_id))
        actor_admin = actor_result.scalars().first()
        actor_level = actor_admin.role_level if actor_admin else 0

        if target_admin.role_level >= actor_level:
            return (
                "Нельзя применить команду к админу с таким же или более высоким уровнем доступа."
            )

        return None
