from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config import settings


def _is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


def _sqlite_path() -> Path | None:
    """Путь к файлу базы для SQLite. None — если база не файловая."""

    if not _is_sqlite():
        return None

    tail = settings.database_url.split("///", 1)[-1]
    tail = tail.split("?", 1)[0]

    if not tail or tail == ":memory:":
        return None

    return Path(tail)


_connect_args = {}
if _is_sqlite():
    # По умолчанию SQLite ждёт блокировку 5 секунд и сдаётся. При рассылке
    # на тысячи пользователей этого мало.
    _connect_args["timeout"] = 30

engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    connect_args=_connect_args,
    pool_pre_ping=True,
)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if _is_sqlite():

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        """WAL и внешние ключи для каждого соединения.

        WAL задаётся один раз на файл, но повторный вызов безвреден,
        а foreign_keys в SQLite выключены по умолчанию именно на уровне
        соединения.
        """

        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


class Base(DeclarativeBase):
    pass


async def init_db():
    # Если база лежит в подкаталоге (data/bot.db), без него будет
    # «unable to open database file» на чистом сервере.
    path = _sqlite_path()
    if path is not None and path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def check_connection() -> None:
    """Пробный запрос на старте.

    Лучше упасть сразу с понятной ошибкой, чем принимать обращения и
    терять их из-за недоступной базы.
    """

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """Аккуратно закрыть соединения при остановке."""

    await engine.dispose()


async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session
