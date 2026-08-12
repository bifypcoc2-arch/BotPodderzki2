"""Безопасные вызовы Telegram API.

Три ситуации, из-за которых бот раньше ломался или терял сообщения:

1. 429 и flood control. Telegram говорит «подожди N секунд», а наивный код
   считает это ошибкой доставки и теряет сообщение.
2. Пользователь заблокировал бота. Повторы бессмысленны, но и паниковать
   нечего: это нормальное событие, особенно при рассылке.
3. Сеть моргнула. Одна потерянная секунда не должна стоить обращения.

Код ответов, пересылок и рассылок должен ходить через эти две функции,
а не писать свои try/except с разным поведением.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, Tuple

from services.telegram_errors import is_user_unreachable, retry_after


logger = logging.getLogger(__name__)

# Сколько раз пробуем при временных проблемах.
DEFAULT_ATTEMPTS = 4

# Если Telegram просит ждать дольше этого, ждать не станем: лучше
# отдать ошибку выше, чем висеть полчаса внутри обработчика.
MAX_FLOOD_WAIT_SECONDS = 90

# Задержка перед повтором при сетевой ошибке: 1, 2, 4 секунды.
NETWORK_BACKOFF_BASE_SECONDS = 1

OK = "ok"
UNREACHABLE = "unreachable"
FAILED = "failed"


def _is_network_error(error: Exception) -> bool:
    """Отличаем «сеть моргнула» от «Telegram сказал нет».

    Смотрим на имена классов, а не импортируем aiohttp: обёртка не должна
    знать, на каком транспорте работает библиотека.
    """

    if isinstance(error, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True

    name = type(error).__name__
    return name.startswith("Client") or name in ("ServerTimeoutError", "ServerDisconnectedError")


async def call_with_retry(
    func: Callable[..., Awaitable[Any]],
    *args,
    attempts: int = DEFAULT_ATTEMPTS,
    **kwargs,
) -> Any:
    """Вызвать метод бота, пережив лимиты и моргание сети.

    Возвращает результат вызова. Ошибки, которые повтором не лечатся
    (нет прав, плохой запрос, удалённая тема), поднимаются сразу — решать
    их должен вызывающий код, у него есть контекст.
    """

    last_error: Optional[Exception] = None

    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            # Остановка бота — не повод для ретраев.
            raise
        except Exception as error:
            last_error = error

            wait = retry_after(error)
            if wait is not None:
                if wait > MAX_FLOOD_WAIT_SECONDS:
                    logger.warning(
                        "Telegram просит ждать %s с — слишком долго, отдаю ошибку", wait
                    )
                    raise

                # +1 секунда запаса: ответ шёл по сети, часы могут расходиться.
                logger.info("Лимит Telegram, жду %s с (попытка %s)", wait, attempt)
                await asyncio.sleep(wait + 1)
                continue

            if _is_network_error(error) and attempt < attempts:
                delay = NETWORK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Сетевая ошибка %s, повтор через %s с", type(error).__name__, delay
                )
                await asyncio.sleep(delay)
                continue

            raise

    # Сюда попадаем, если кончились попытки на лимитах или сети.
    if last_error is not None:
        raise last_error

    raise RuntimeError("call_with_retry вызван с attempts <= 0")


async def try_send(
    func: Callable[..., Awaitable[Any]],
    *args,
    attempts: int = DEFAULT_ATTEMPTS,
    **kwargs,
) -> Tuple[str, Any]:
    """Как call_with_retry, но без исключений.

    Возвращает пару (статус, результат или ошибка):

    - OK — доставлено, вторым элементом ответ Telegram;
    - UNREACHABLE — бот заблокирован или аккаунт удалён, повторять не надо;
    - FAILED — всё остальное, вторым элементом исключение.

    Годится там, где один неудавшийся адресат не должен рвать цикл:
    рассылка, уведомления, предложение игр при ожидании.
    """

    try:
        result = await call_with_retry(func, *args, attempts=attempts, **kwargs)
        return OK, result
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if is_user_unreachable(error):
            return UNREACHABLE, error

        return FAILED, error
