import asyncio
import logging
from datetime import datetime

from database.database import async_session_maker
from database.models import User, Admin, Topic, UserPet, Stats, ShopItem, Broadcast


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_database():
    """Проверка состояния базы данных и статистика"""

    async with async_session_maker() as session:
        from sqlalchemy import select, func

        # Пользователи
        users_count = await session.scalar(select(func.count(User.user_id)))
        active_users = await session.scalar(
            select(func.count(User.user_id)).where(User.is_active == True)
        )

        # Администраторы
        admins_count = await session.scalar(select(func.count(Admin.user_id)))

        # Темы поддержки
        topics_count = await session.scalar(select(func.count(Topic.id)))

        # Питомцы
        pets_count = await session.scalar(select(func.count(UserPet.user_id)))

        # Предметы в магазине
        shop_items_count = await session.scalar(select(func.count(ShopItem.id)))

        # Рассылки
        broadcasts_count = await session.scalar(select(func.count(Broadcast.id)))

        print("\n" + "="*50)
        print("СТАТИСТИКА БАЗЫ ДАННЫХ")
        print("="*50)
        print(f"Всего пользователей: {users_count}")
        print(f"Активных пользователей: {active_users}")
        print(f"Администраторов: {admins_count}")
        print(f"Тем поддержки: {topics_count}")
        print(f"Питомцев: {pets_count}")
        print(f"Предметов в магазине: {shop_items_count}")
        print(f"Рассылок: {broadcasts_count}")
        print("="*50 + "\n")

        # Список администраторов
        if admins_count > 0:
            result = await session.execute(select(Admin))
            admins = result.scalars().all()

            print("АДМИНИСТРАТОРЫ:")
            print("-"*50)
            for admin in admins:
                print(f"User ID: {admin.user_id}")
                print(f"  Роль: {admin.role.value} (уровень {admin.role_level})")
                print(f"  Добавлен: {admin.added_at.strftime('%Y-%m-%d %H:%M:%S')}")
                print()

        # Последние темы
        if topics_count > 0:
            from database.models import TopicStatus
            result = await session.execute(
                select(Topic).order_by(Topic.created_at.desc()).limit(5)
            )
            recent_topics = result.scalars().all()

            print("ПОСЛЕДНИЕ ТЕМЫ ПОДДЕРЖКИ:")
            print("-"*50)
            for topic in recent_topics:
                print(f"ID: {topic.id} | User: {topic.user_id} | Status: {topic.status.value}")
                print(f"  Создана: {topic.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                print()


async def main():
    try:
        await check_database()
    except Exception as e:
        logger.error(f"Ошибка при проверке базы данных: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
