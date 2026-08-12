"""Разбор ошибок Telegram API.

Telegram не даёт машиночитаемых кодов для большинства случаев — только
текстовое description. Поэтому разбор по подстрокам — штатный способ.
Собрано в одном месте, чтобы не дублировать строки по сервисам.
"""

import re
from typing import Optional


# Тема удалена или не существует.
_THREAD_MISSING_MARKERS = (
    "message thread not found",
    "topic_deleted",
    "topic deleted",
    "thread not found",
)

# Пользователь недоступен: заблокировал бота, удалил аккаунт или никогда не писал.
_USER_UNREACHABLE_MARKERS = (
    "bot was blocked by the user",
    "user is deactivated",
    "chat not found",
    "bot can't initiate conversation",
)

# Превышен лимит запросов.
_FLOOD_MARKERS = (
    "too many requests",
    "retry after",
    "flood",
)

# Сколько ждать, если Telegram сказал "слишком часто", но не назвал срок.
DEFAULT_FLOOD_WAIT_SECONDS = 5


def _description(error: Exception) -> str:
    description = getattr(error, "description", None)
    return (description or str(error)).lower()


def is_thread_missing(error: Exception) -> bool:
    """Тема удалена в Telegram, хотя в базе ещё числится открытой."""
    return any(marker in _description(error) for marker in _THREAD_MISSING_MARKERS)


def is_user_unreachable(error: Exception) -> bool:
    """Писать этому пользователю бесполезно — повторять не надо."""
    return any(marker in _description(error) for marker in _USER_UNREACHABLE_MARKERS)


def retry_after(error: Exception) -> Optional[int]:
    """Сколько секунд ждать при флуд-контроле. None — ошибка не про лимиты.

    Telegram кладёт точное значение в parameters.retry_after. Если по какой-то
    причине разобрать JSON не вышло, достаём число из текста, а в крайнем
    случае берём значение по умолчанию — лучше подождать лишнее, чем
    продолжить долбить API и получить бан подольше.
    """
    payload = getattr(error, "result_json", None)
    if isinstance(payload, dict):
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            value = parameters.get("retry_after")
            if isinstance(value, int) and value >= 0:
                return value

    description = _description(error)
    if not any(marker in description for marker in _FLOOD_MARKERS):
        return None

    match = re.search(r"retry after (\d+)", description)
    if match:
        return int(match.group(1))

    return DEFAULT_FLOOD_WAIT_SECONDS
