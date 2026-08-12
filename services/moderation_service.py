import logging
from datetime import datetime

from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import async_session_maker
from database.models import (
    ActionType,
    Admin,
    AdminLog,
    AdminStats,
    Ban,
    Stats,
    Topic,
    User,
)
from services.anonymity import anonymous_code


logger = logging.getLogger(__name__)


class ModerationService:
    """Баны и статистика сообщений. Доступно только владельцу и совладельцу
    (проверка уровня живёт в хендлерах)."""

    async def is_banned(self, user_id: int) -> bool:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Ban).where(Ban.user_id == user_id, Ban.is_active.is_(True))
            )
            return result.scalars().first() is not None

    async def increment_admin_messages(self, admin_user_id: int):
        async with async_session_maker() as session:
            result = await session.execute(
                select(AdminStats).where(AdminStats.user_id == admin_user_id)
            )
            row = result.scalars().first()

            if not row:
                row = AdminStats(user_id=admin_user_id, messages_sent=0)
                session.add(row)

            row.messages_sent = (row.messages_sent or 0) + 1
            row.last_message_at = datetime.utcnow()
            await session.commit()

    async def _resolve_user_id(self, session: AsyncSession, token: str) -> int | None:
        """Принимает анонимный код (#A7F3C2) или сырой Telegram ID."""
        token = token.strip().lstrip("#").upper()

        if not token:
            return None

        if token.isdigit():
            return int(token)

        result = await session.execute(select(User.user_id))
        for user_id in result.scalars().all():
            if anonymous_code(user_id) == token:
                return user_id

        return None

    async def _topic_user_id(self, session: AsyncSession, thread_id: int | None) -> int | None:
        if not thread_id:
            return None

        result = await session.execute(
            select(Topic).where(Topic.topic_id == thread_id)
        )
        topic = result.scalars().first()
        return topic.user_id if topic else None

    async def _target_user_id(
        self, session: AsyncSession, message: Message, argument: str | None
    ) -> int | None:
        if argument:
            return await self._resolve_user_id(session, argument)
        return await self._topic_user_id(session, message.message_thread_id)

    async def handle_ban_command(self, message: Message, bot: AsyncTeleBot):
        parts = (message.text or "").split(maxsplit=2)
        argument = parts[1] if len(parts) > 1 else None
        reason = parts[2].strip() if len(parts) > 2 else None

        async with async_session_maker() as session:
            user_id = await self._target_user_id(session, message, argument)

            if not user_id:
                await bot.reply_to(
                    message,
                    "Не нашёл пользователя.\n"
                    "Используйте /ban внутри темы обращения или /ban <код|user_id> [причина]"
                )
                return

            admin_result = await session.execute(
                select(Admin).where(Admin.user_id == user_id)
            )
            if admin_result.scalars().first():
                await bot.reply_to(message, "⛔ Нельзя забанить администратора.")
                return

            existing = await session.execute(
                select(Ban).where(Ban.user_id == user_id, Ban.is_active.is_(True))
            )
            if existing.scalars().first():
                await bot.reply_to(
                    message, f"Пользователь #{anonymous_code(user_id)} уже заблокирован."
                )
                return

            session.add(
                Ban(
                    user_id=user_id,
                    banned_by=message.from_user.id,
                    reason=reason,
                    is_active=True
                )
            )
            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.USER_BANNED,
                    details=f"#{anonymous_code(user_id)}" + (f": {reason}" if reason else "")
                )
            )
            await session.commit()

        text = f"🚫 Пользователь #{anonymous_code(user_id)} заблокирован."
        if reason:
            text += f"\nПричина: {reason}"
        await bot.reply_to(message, text)

        notice = "⛔ Вы заблокированы в службе поддержки."
        if reason:
            notice += f"\nПричина: {reason}"
        try:
            await bot.send_message(user_id, notice)
        except Exception as e:
            logger.error(f"Failed to notify banned user {user_id}: {e}")

    async def handle_unban_command(self, message: Message, bot: AsyncTeleBot):
        parts = (message.text or "").split(maxsplit=1)
        argument = parts[1] if len(parts) > 1 else None

        async with async_session_maker() as session:
            user_id = await self._target_user_id(session, message, argument)

            if not user_id:
                await bot.reply_to(
                    message,
                    "Не нашёл пользователя.\nИспользуйте /unban <код|user_id>"
                )
                return

            result = await session.execute(
                select(Ban).where(Ban.user_id == user_id, Ban.is_active.is_(True))
            )
            ban = result.scalars().first()

            if not ban:
                await bot.reply_to(
                    message, f"Пользователь #{anonymous_code(user_id)} не заблокирован."
                )
                return

            ban.is_active = False
            ban.unbanned_by = message.from_user.id
            ban.unbanned_at = datetime.utcnow()

            session.add(
                AdminLog(
                    admin_user_id=message.from_user.id,
                    action_type=ActionType.USER_UNBANNED,
                    details=f"#{anonymous_code(user_id)}"
                )
            )
            await session.commit()

        await bot.reply_to(
            message, f"✅ Блокировка с #{anonymous_code(user_id)} снята."
        )

        try:
            await bot.send_message(user_id, "✅ Блокировка снята, вы снова можете писать в поддержку.")
        except Exception as e:
            logger.error(f"Failed to notify unbanned user {user_id}: {e}")

    async def send_ban_list(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            result = await session.execute(
                select(Ban)
                .where(Ban.is_active.is_(True))
                .order_by(desc(Ban.created_at))
                .limit(30)
            )
            bans = result.scalars().all()

        if not bans:
            await bot.reply_to(message, "Активных блокировок нет.")
            return

        lines = [f"🚫 Активных блокировок: {len(bans)}", ""]
        for ban in bans:
            line = f"#{anonymous_code(ban.user_id)} — {ban.created_at.strftime('%d.%m.%Y')}"
            if ban.reason:
                line += f" — {ban.reason}"
            lines.append(line)

        await bot.reply_to(message, "\n".join(lines))

    async def send_message_stats(self, message: Message, bot: AsyncTeleBot):
        async with async_session_maker() as session:
            users_result = await session.execute(
                select(Stats)
                .where(Stats.messages_sent > 0)
                .order_by(desc(Stats.messages_sent))
                .limit(10)
            )
            top_users = users_result.scalars().all()

            all_users_result = await session.execute(select(Stats.messages_sent))
            total_user_messages = sum(count or 0 for count in all_users_result.scalars().all())

            admins_result = await session.execute(
                select(AdminStats)
                .where(AdminStats.messages_sent > 0)
                .order_by(desc(AdminStats.messages_sent))
                .limit(10)
            )
            top_admins = admins_result.scalars().all()

            roles_result = await session.execute(select(Admin))
            roles = {admin.user_id: admin.role.value for admin in roles_result.scalars().all()}

        lines = ["📊 Статистика сообщений", ""]

        lines.append(f"Всего от пользователей: {total_user_messages}")
        lines.append("")
        lines.append("Топ пользователей:")

        if top_users:
            for index, stats in enumerate(top_users, start=1):
                lines.append(f"{index}. #{anonymous_code(stats.user_id)} — {stats.messages_sent}")
        else:
            lines.append("— пока пусто")

        lines.append("")
        lines.append("Топ админов по ответам:")

        if top_admins:
            for index, admin_stats in enumerate(top_admins, start=1):
                role = roles.get(admin_stats.user_id, "—")
                lines.append(
                    f"{index}. {admin_stats.user_id} ({role}) — {admin_stats.messages_sent}"
                )
        else:
            lines.append("— пока пусто")

        await bot.reply_to(message, "\n".join(lines))
