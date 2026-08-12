import asyncio
import logging
import signal
from pathlib import Path

from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_storage import StateMemoryStorage, StatePickleStorage
from sqlalchemy import select

from config import settings
from database.database import (
    init_db,
    async_session_maker,
    check_connection,
    dispose_engine,
)
from handlers import support, admin, broadcast, miniapp
from handlers import chat_admin
from logging_setup import install_exception_handlers, set_alert_hook, setup_logging
from middlewares.antispam import AntiSpamMiddleware
from services.alerts import notify_owners
from services.backup_service import BackupService
from services.recovery import recover_stuck_broadcasts
from services.telegram_safe import try_send, UNREACHABLE


setup_logging()
logger = logging.getLogger(__name__)


def _build_state_storage():
    """Состояние диалогов на диске, а не в памяти.

    С StateMemoryStorage любой перезапуск посреди создания рассылки терял
    шаг, и админ отправлял контент в пустоту: бот уже не ждал его.
    """

    try:
        path = Path(settings.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        return StatePickleStorage(file_path=str(path))
    except Exception:
        logger.exception("Не удалось открыть файл состояний, работаю в памяти")
        return StateMemoryStorage()


bot = AsyncTeleBot(
    settings.bot_token,
    state_storage=_build_state_storage(),
    use_class_middlewares=True,
)


# Фоновой задачи деградации здесь нет сознательно. Параметры питомца
# пересчитываются лениво — в момент любого обращения к питомцу, см. PetService.


async def queue_waiting_task():
    """Фоновая задача для отправки предложения игр при долгом ожидании"""
    from datetime import datetime, timedelta
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    from database.models import Topic, TopicStatus

    while True:
        await asyncio.sleep(60)

        threshold_time = datetime.utcnow() - timedelta(minutes=settings.queue_wait_minutes)

        async with async_session_maker() as session:
            result = await session.execute(
                select(Topic).where(
                    Topic.status == TopicStatus.OPEN,
                    Topic.game_offer_sent == False,
                    Topic.created_at <= threshold_time
                )
            )
            topics = result.scalars().all()

            sent = 0
            for topic in topics:
                markup = InlineKeyboardMarkup()
                webapp = WebAppInfo(url=f"{settings.mini_app_url}#games")
                button = InlineKeyboardButton(text="🎮 Поиграть в игры", web_app=webapp)
                markup.add(button)

                status, payload = await try_send(
                    bot.send_message,
                    topic.user_id,
                    "Пока ожидаете ответ, можете поиграть в мини-игры и заработать монеты для питомца!",
                    reply_markup=markup
                )

                # Пометку ставим и при успехе, и когда писать некому: иначе бот
                # будет каждую минуту штурмовать тех, кто его заблокировал.
                if status == UNREACHABLE:
                    topic.game_offer_sent = True
                    logger.info("Предложение игр не доставлено %s: %s", topic.user_id, payload)
                    continue

                if status != "ok":
                    logger.warning(
                        "Не удалось отправить предложение игр %s: %s", topic.user_id, payload
                    )
                    continue

                topic.game_offer_sent = True
                sent += 1

            if topics:
                await session.commit()
                logger.info("Предложение игр ушло %s пользователям", sent)


async def backup_task():
    """Регулярные бэкапы базы."""
    service = BackupService()

    if not settings.backup_enabled:
        logger.info("Автобэкапы отключены настройкой")
        return

    if not service.is_supported():
        logger.warning(
            "Автобэкапы недоступны для этой базы. Настройте бэкап средствами СУБД."
        )
        return

    # Первый снимок — сразу после старта: самый рискованный момент —
    # только что выложенная новая версия.
    interval = max(1, settings.backup_interval_hours) * 3600

    while True:
        await service.create_backup()
        await asyncio.sleep(interval)


async def limiter_cleanup_task(middleware: AntiSpamMiddleware):
    """Чистка счётчиков антиспама, чтобы они не росли бесконечно."""

    while True:
        await asyncio.sleep(600)
        middleware.limiter.cleanup()


async def supervise(name: str, factory):
    """Перезапускать задачу, если она упала.

    Раньше ошибка внутри фонового цикла могла тихо его завершить: бот
    вроде работает, а бэкапы больше не снимаются. Задержка растёт до
    5 минут, чтобы сломанная насовсем задача не крутилась впустую.
    """

    delay = 5

    while True:
        try:
            await factory()
            logger.info("Задача %s завершилась штатно", name)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Задача %s упала, перезапуск через %s с", name, delay)
            await notify_owners(
                bot,
                f"Фоновая задача «{name}» упала: {type(error).__name__}: {error}\n"
                f"Перезапущу автоматически через {delay} с.",
                key=f"task-failed:{name}",
            )

            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


def _install_alert_hook(loop: asyncio.AbstractEventLoop) -> None:
    """Позволить логгеру сообщать о падениях в Telegram.

    Обработчики исключений синхронные, поэтому отправку кладём в цикл
    событий через call_soon_threadsafe.
    """

    def hook(text: str) -> None:
        try:
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    notify_owners(bot, text, key="unhandled")
                )
            )
        except RuntimeError:
            # Цикл уже закрыт — остаётся только запись в лог.
            logger.warning("Оповещение не отправлено: цикл событий остановлен")

    set_alert_hook(hook)


async def main():
    loop = asyncio.get_running_loop()
    install_exception_handlers(loop)
    _install_alert_hook(loop)

    await init_db()
    await check_connection()

    # Следы предыдущего завершения разбираем до приёма сообщений.
    stuck = await recover_stuck_broadcasts()

    antispam = AntiSpamMiddleware(bot)
    bot.setup_middleware(antispam)

    support.register_handlers(bot)
    # Раньше admin: там есть обработчик всех сообщений группы, а сообщение
    # достаётся первому подходящему обработчику.
    chat_admin.register_handlers(bot)
    admin.register_handlers(bot)
    broadcast.register_handlers(bot)
    miniapp.register_handlers(bot)

    tasks = [
        asyncio.create_task(supervise("предложение игр", queue_waiting_task)),
        asyncio.create_task(supervise("бэкапы", backup_task)),
        asyncio.create_task(
            supervise("чистка антиспама", lambda: limiter_cleanup_task(antispam))
        ),
    ]

    # Остановка по сигналу: systemd при restart шлёт SIGTERM, и без этого
    # бот умирал, не закрыв ни сессию Telegram, ни соединения базы.
    stopping = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            # Windows часть сигналов не умеет — переживём.
            pass

    logger.info("Бот запущен...")

    if stuck:
        await notify_owners(
            bot,
            "После перезапуска нашлись недосланные рассылки: "
            + ", ".join(f"#{item}" for item in stuck)
            + ".\nОни вернулись в черновики, проверьте их в /ads перед повторной отправкой.",
            key="stuck-broadcasts",
        )

    if settings.alert_on_start:
        await notify_owners(bot, "Бот запущен и принимает обращения.", key="startup")

    polling = asyncio.create_task(bot.infinity_polling(logger_level=logging.WARNING))

    try:
        done, _ = await asyncio.wait(
            [polling, asyncio.create_task(stopping.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Если завершился именно polling — значит, внутри что-то сломалось.
        if polling in done:
            error = polling.exception()
            if error is not None:
                logger.critical("Опрос Telegram остановлен ошибкой", exc_info=error)
                await notify_owners(
                    bot,
                    f"Бот остановил приём сообщений: {type(error).__name__}: {error}",
                    key="polling-crash",
                )
                raise error

            logger.warning("Опрос Telegram завершён")
    finally:
        logger.info("Остановка...")

        polling.cancel()
        for task in tasks:
            task.cancel()

        # Ждём завершения, иначе задача может оборваться посередине записи.
        await asyncio.gather(polling, *tasks, return_exceptions=True)

        try:
            await bot.close_session()
        except Exception:
            logger.exception("Не удалось закрыть сессию Telegram")

        # Финальный бэкап перед выходом: самые свежие данные точно
        # окажутся в копии, если остановка плановая — например, перед апдейтом.
        if settings.backup_enabled:
            try:
                await BackupService().create_backup()
            except Exception:
                logger.exception("Финальный бэкап не создан")

        await dispose_engine()
        set_alert_hook(None)

        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено с клавиатуры")
