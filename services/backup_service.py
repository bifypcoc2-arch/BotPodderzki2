"""Бэкапы базы.

Главный сценарий, от которого страхуемся: диск умер, файл базы стёрли
вместе с каталогом или кто-то выполнил лишний запрос руками. Без
бэкапа это потеря всей переписки, питомцев и балансов.

Работает только для SQLite. Для PostgreSQL бэкап делается средствами СУБД
(pg_dump по cron), и пытаться имитировать это из бота — вредно.
"""

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import settings
from database.database import engine, _is_sqlite, _sqlite_path


logger = logging.getLogger(__name__)

BACKUP_PREFIX = "bot-"
BACKUP_SUFFIX = ".db"


class BackupService:
    def __init__(self) -> None:
        self.directory = Path(settings.backup_dir)

    def is_supported(self) -> bool:
        """Есть ли что вообще копировать."""

        return _is_sqlite() and _sqlite_path() is not None

    async def create_backup(self) -> Optional[Path]:
        """Снять копию и почистить старые. None — если не вышло."""

        if not self.is_supported():
            logger.info(
                "Автобэкап пропущен: база не SQLite. Настройте pg_dump средствами СУБД."
            )
            return None

        self.directory.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"

        if target.exists():
            # Два бэкапа в одну секунду — редкость, но перезатирать не станем.
            logger.debug("Бэкап %s уже есть", target)
            return target

        try:
            await self._vacuum_into(target)
        except Exception as error:
            logger.warning("VACUUM INTO не сработал (%s), копирую файл", error)
            try:
                await self._copy_file(target)
            except Exception:
                logger.exception("Бэкап не создан")
                return None

        size = target.stat().st_size if target.exists() else 0
        logger.info("Бэкап готов: %s (%s КБ)", target, size // 1024)

        self._rotate()
        return target

    async def _vacuum_into(self, target: Path) -> None:
        """Цельный снимок силами самого SQLite.

        В отличие от копирования файла, здесь не важно, идёт ли в этот
        момент запись: результат всегда согласованный. Требует SQLite 3.27+.
        """

        # Кавычки в имени файла невозможны (имя собираем мы сами из даты),
        # но путь к каталогу задаёт пользователь — экранируем на всякий случай.
        literal = str(target.resolve()).replace("'", "''")

        async with engine.connect() as conn:
            await conn.exec_driver_sql(f"VACUUM INTO '{literal}'")

    async def _copy_file(self, target: Path) -> None:
        """Запасной вариант: чекпоинт WAL и копия файла.

        Сначала сбрасываем WAL в основной файл, иначе в копии не будет
        самых свежих изменений.
        """

        source = _sqlite_path()
        if source is None or not source.exists():
            raise FileNotFoundError("Файл базы не найден")

        async with engine.connect() as conn:
            await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

        await asyncio.to_thread(shutil.copy2, source, target)

    def _rotate(self) -> None:
        """Оставить только последние backup_keep копий.

        Без ротации бэкапы сами забьют диск и положат бота — то есть
        страховка станет причиной аварии.
        """

        keep = max(1, settings.backup_keep)

        try:
            backups = sorted(
                self.directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"),
                key=lambda path: path.name,
            )
        except OSError:
            logger.exception("Не удалось просмотреть каталог бэкапов")
            return

        for path in backups[:-keep]:
            try:
                path.unlink()
                logger.info("Удалён старый бэкап %s", path.name)
            except OSError as error:
                logger.warning("Не удалось удалить %s: %s", path.name, error)
