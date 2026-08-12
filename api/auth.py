import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from aiohttp import web

from config import settings


logger = logging.getLogger(__name__)

# Заголовок, в котором мини-приложение присылает Telegram.WebApp.initData.
INIT_DATA_HEADER = "X-Telegram-Init-Data"

# Сколько живёт подпись. Защищает от повторного использования старого initData.
MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


def _secret_key(bot_token: str) -> bytes:
    """Секретный ключ по схеме Telegram: HMAC-SHA256("WebAppData", токен бота)."""
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def parse_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = MAX_AUTH_AGE_SECONDS
) -> dict | None:
    """Проверяет подпись initData и возвращает его поля.

    Возвращает None, если подпись не сошлась, полей не хватает
    или данные слишком старые.
    """
    if not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    # Строка проверки: все поля кроме hash, отсортированные по имени,
    # в виде key=value через перевод строки.
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))

    calculated_hash = hmac.new(
        _secret_key(bot_token),
        data_check_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date", "")
    if not auth_date.isdigit():
        return None

    if max_age and time.time() - int(auth_date) > max_age:
        return None

    return pairs


def extract_user_id(pairs: dict) -> int | None:
    """Достаёт Telegram ID из уже проверенных данных."""
    raw_user = pairs.get("user")
    if not raw_user:
        return None

    try:
        user = json.loads(raw_user)
    except (json.JSONDecodeError, TypeError):
        return None

    user_id = user.get("id")
    if not isinstance(user_id, int):
        return None

    return user_id


@web.middleware
async def telegram_auth_middleware(request: web.Request, handler):
    """Пускает к /api/ только запросы с корректной подписью Telegram.

    После проверки кладёт настоящий user_id в request["user_id"].
    Обработчики берут ID только оттуда и никогда — из тела запроса.
    """
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get(INIT_DATA_HEADER, "")
    pairs = parse_init_data(init_data, settings.bot_token)

    if not pairs:
        logger.warning(f"Rejected unsigned request to {request.path}")
        return web.json_response(
            {"error": "unauthorized", "message": "Откройте приложение через Telegram."},
            status=401
        )

    user_id = extract_user_id(pairs)
    if not user_id:
        logger.warning(f"Signed request to {request.path} without user")
        return web.json_response(
            {"error": "unauthorized", "message": "Не удалось определить пользователя."},
            status=401
        )

    request["user_id"] = user_id
    return await handler(request)
