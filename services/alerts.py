"""Служебные оповещения владельцу и совладельцу.

Смысл: если бот упал или фоновая задача сломалась, об этом надо узнать
сразу, а не от клиентов через сутки. Адресатов берём из таблицы
админов по уровню прав, а не из .env: так список не расходится с
реальными ролями.
"""

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select

from config import settings
from database.database import async_session_maker
from database.models import Admin, OWNER_LEVEL
from services.telegram_safe import try_send, OK


logger = logging.getLogger(__name__)

# Когда какой текст отправляли последний раз.
_last_sent: dict[str, float] = {}
_guard = asyncio.Lock()


async def _owner_ids() -> list[int]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Admin.user_id).where(Admin.role_level >= OWNER_LEVEL)
        )
        return list(result.scalars().all())


async def notify_owners(bot, text: str, *, key: Optional[str] = None) -> None:
    """Отправить служебное сообщение владельцам.

    key — чем считать «тем же самым сообщением» для дедубликации.
    По умолчанию — сам текст.

    Функция никогда не поднимает исключений: её вызывают из обработчиков
    ошибок, и падение внутри оповещения замаскировало бы исходную причину.
    """

    if not settings.alerts_enabled:
        return

    marker = key or text
    now = time.monotonic()
    window = settings.alert_repeat_minutes * 60

    try:
        async with _guard:
            previous = _last_sent.get(marker)
            if previous is not None and now - previous < window:
                logger.debug("Оповещение пропущено (повтор): %s", marker)
                return

            _last_sent[marker] = now

        owners = await _owner_ids()
        if not owners:
            logger.warning("Некому сообщить: в базе нет владельцев. Текст: %s", text)
            return

        for user_id in owners:
            status, payload = await try_send(
                bot.send_message, user_id, f"\u26a0\ufe0f Служебное сообщение\n\n{text}"
            )
            if status != OK:
                logger.warning(
                    "Не удалось доставить оповещение владельцу %s: %s", user_id, payload
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Ошибка при отправке служебного оповещения")


def reset_dedup_state() -> None:
    """Сбросить память о отправленных оповещениях. Нужно только в тестах."""

    _last_sent.clear()
