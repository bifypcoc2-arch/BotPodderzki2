"""Настройка логов.

До этого был basicConfig без файла: вся история жила в консоли и
умирала вместе с процессом. Теперь есть файл с ротацией, чтобы после
падения было что читать, и чтобы этот файл не съел диск.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

from config import settings


LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Эти логгеры на INFO пишут по строке на каждый запрос к Telegram и
# каждый SQL. На живом боте из-за этого не видно собственных сообщений.
NOISY_LOGGERS = (
    "aiohttp.access",
    "aiohttp.client",
    "asyncio",
    "sqlalchemy.engine.Engine",
)

_alert_hook: Optional[Callable[[str], None]] = None


def setup_logging() -> logging.Logger:
    """Консоль + файл с ротацией. Возвращает корневой логгер."""

    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Функцию могут вызвать дважды (например, из бота и из веб-сервера),
    # а дублированные обработчики дают каждую строку по два раза.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        directory = Path(settings.log_dir)
        directory.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            directory / "bot.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as error:
        # Нет прав на каталог — не повод не запускать бота.
        root.warning("Не удалось открыть файл лога: %s", error)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return root


def set_alert_hook(hook: Optional[Callable[[str], None]]) -> None:
    """Куда сообщать о падениях, которые никто не перехватил.

    Задаётся из main, чтобы этот модуль не знал ни про бота, ни про базу.
    """

    global _alert_hook
    _alert_hook = hook


def install_exception_handlers(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Ловит то, что иначе утекло бы в пустоту.

    Два канала: обычные исключения и исключения внутри asyncio-задач.
    Второе важнее: упавшая фоновая задача не роняет процесс и раньше
    просто молча переставала работать.
    """

    logger = logging.getLogger("unhandled")

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical(
            "Необработанное исключение",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        _report(f"Необработанное исключение: {exc_type.__name__}: {exc_value}")

    sys.excepthook = handle_exception

    def handle_async_exception(_loop, context):
        error = context.get("exception")
        message = context.get("message") or "Ошибка в asyncio"

        if error is not None:
            logger.critical("%s", message, exc_info=error)
            _report(f"{message}: {type(error).__name__}: {error}")
            return

        logger.critical("%s", message)
        _report(message)

    target = loop or asyncio.get_event_loop()
    target.set_exception_handler(handle_async_exception)


def _report(text: str) -> None:
    if _alert_hook is None:
        return

    try:
        _alert_hook(text)
    except Exception:
        # Сломавшееся оповещение не должно мешать логированию.
        logging.getLogger("unhandled").exception("Не удалось отправить оповещение")
