"""Антиспам для личных сообщений.

Проблема, которую решаем: каждое сообщение в личке — это запись в базу
плюс пересылка в группу. Кто угодно может зажать отправку и засыпать
тему тысячей строк, а бот за это получит лимит от Telegram и перестанет
отвечать всем остальным.

Почему middleware, а не проверка в обработчике: лимит должен работать
для всех типов сообщений сразу, включая те, которые добавят позже.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from sqlalchemy import select
from telebot.asyncio_handler_backends import BaseMiddleware, CancelUpdate

from config import settings
from database.database import async_session_maker
from database.models import Admin


logger = logging.getLogger(__name__)

BLOCK_NOTICE = (
    "\u23f3 Слишком много сообщений подряд. Подождите немного — всё, что вы "
    "успели написать, уже у поддержки."
)

# Сколько доверять кэшу админства. Нового админа антиспам увидит с
# задержкой до пяти минут — для лимита это несущественно.
ADMIN_CACHE_TTL_SECONDS = 300


class RateLimiter:
    """Окно событий на пользователя.

    Вынесен отдельно от middleware, чтобы логику можно было проверить
    тестами без Telegram.
    """

    def __init__(
        self,
        *,
        window_seconds: int,
        max_messages: int,
        block_seconds: int,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.block_seconds = block_seconds

        self._events: Dict[int, Deque[float]] = defaultdict(deque)
        self._blocked_until: Dict[int, float] = {}

    def check(self, user_id: int, now: float | None = None) -> Tuple[bool, bool]:
        """Пропускаем ли сообщение.

        Возвращает (пропустить, предупредить). Предупреждаем только в момент
        блокировки: если отвечать на каждое заблокированное сообщение, получится
        тот же спам, только от имени бота.
        """

        moment = time.monotonic() if now is None else now

        blocked_until = self._blocked_until.get(user_id)
        if blocked_until is not None:
            if moment < blocked_until:
                return False, False

            # Блокировка истекла — начинаем счёт заново.
            del self._blocked_until[user_id]
            self._events.pop(user_id, None)

        events = self._events[user_id]
        events.append(moment)

        threshold = moment - self.window_seconds
        while events and events[0] < threshold:
            events.popleft()

        if len(events) > self.max_messages:
            self._blocked_until[user_id] = moment + self.block_seconds
            events.clear()
            return False, True

        return True, False

    def forget(self, user_id: int) -> None:
        self._events.pop(user_id, None)
        self._blocked_until.pop(user_id, None)

    def cleanup(self, now: float | None = None) -> None:
        """Выбросить тех, кто давно не писал.

        Без этого словари растут на каждого пользователя за всю жизнь
        процесса. На десятках тысяч обращений это уже заметная память.
        """

        moment = time.monotonic() if now is None else now
        threshold = moment - self.window_seconds

        for user_id in [uid for uid, events in self._events.items()
                        if not events or events[-1] < threshold]:
            if user_id not in self._blocked_until:
                self._events.pop(user_id, None)

        for user_id in [uid for uid, until in self._blocked_until.items() if until < moment]:
            self._blocked_until.pop(user_id, None)


class AntiSpamMiddleware(BaseMiddleware):
    """Отбрасывает поток сообщений из одной лички.

    Группа поддержки не трогается вообще: там работают сотрудники,
    и обрезать им ответы — последнее, что нужно. Админы в личке тоже
    освобождены: у них там мастер рассылки.
    """

    def __init__(self, bot) -> None:
        super().__init__()

        self.bot = bot
        self.update_types = ["message"]

        self.limiter = RateLimiter(
            window_seconds=settings.antispam_window_seconds,
            max_messages=settings.antispam_max_messages,
            block_seconds=settings.antispam_block_seconds,
        )

        self._admin_cache: Dict[int, Tuple[bool, float]] = {}

    async def pre_process(self, message, data):
        if not settings.antispam_enabled:
            return

        # Служебные апдейты без автора или чата не наша забота.
        chat = getattr(message, "chat", None)
        author = getattr(message, "from_user", None)
        if chat is None or author is None:
            return

        if chat.type != "private":
            return

        allowed, warn = self.limiter.check(author.id)
        if allowed:
            return

        # Запрос в базу делаем только здесь — для тех, кто уже перебрал
        # лимит. На каждом сообщении проверять админство было бы дорого.
        if await self._is_admin(author.id):
            self.limiter.forget(author.id)
            return

        if warn:
            logger.info("Антиспам: притормозил пользователя %s", author.id)

            # Импорт внутри функции: иначе получается круг импортов через
            # services.telegram_safe → services.telegram_errors на старте модуля.
            from services.telegram_safe import try_send

            await try_send(self.bot.send_message, chat.id, BLOCK_NOTICE)

        return CancelUpdate()

    async def post_process(self, message, data, exception):
        # Ничего не делаем, но метод обязателен для BaseMiddleware.
        return

    async def _is_admin(self, user_id: int) -> bool:
        cached = self._admin_cache.get(user_id)
        now = time.monotonic()

        if cached is not None and now - cached[1] < ADMIN_CACHE_TTL_SECONDS:
            return cached[0]

        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(Admin.user_id).where(Admin.user_id == user_id)
                )
                is_admin = result.scalar_one_or_none() is not None
        except Exception:
            # База недоступна — считаем обычным пользователем и притормаживаем.
            logger.exception("Антиспам: не удалось проверить роль %s", user_id)
            return False

        self._admin_cache[user_id] = (is_admin, now)
        return is_admin
