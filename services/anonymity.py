import hashlib


def anonymous_code(user_id: int) -> str:
    """Стабильный анонимный код обращения.

    Один и тот же пользователь всегда получает один и тот же код,
    но по коду нельзя восстановить Telegram ID.

    Вынесено в отдельный модуль, чтобы support_service и moderation_service
    могли пользоваться кодом без циклического импорта.
    """
    digest = hashlib.sha256(f"support:{user_id}".encode("utf-8")).hexdigest()
    return digest[:6].upper()
