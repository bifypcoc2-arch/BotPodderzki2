"""Приведение базы в порядок после перезапуска.

Задача одна: убрать следы неаккуратного выключения. Запускается один
раз на старте, до начала приёма сообщений.
"""

import logging
from typing import List

from sqlalchemy import select

from database.database import async_session_maker
from database.models import Broadcast, BroadcastStatus


logger = logging.getLogger(__name__)


async def recover_stuck_broadcasts() -> List[int]:
    """Вернуть зависшие рассылки в черновики.

    Статус SENDING значит «прямо сейчас раздаётся». Процесс раздачи
    живёт только в памяти, поэтому после рестарта такой статус — враньё:
    никто её больше не раздаёт.

    Возвращаем в DRAFT, а не в SENT: админ сам решит, досылать или нет,
    открыв /ads. Счётчики sent_count и failed_count не трогаем — видно, сколько
    ушло до падения.
    """

    async with async_session_maker() as session:
        result = await session.execute(
            select(Broadcast).where(Broadcast.status == BroadcastStatus.SENDING)
        )
        stuck = list(result.scalars().all())

        if not stuck:
            return []

        for broadcast in stuck:
            broadcast.status = BroadcastStatus.DRAFT
            logger.warning(
                "Рассылка #%s висела в статусе «отправляется» (ушло %s, ошибок %s), "
                "вернул в черновики",
                broadcast.id,
                broadcast.sent_count,
                broadcast.failed_count,
            )

        await session.commit()

        return [broadcast.id for broadcast in stuck]
