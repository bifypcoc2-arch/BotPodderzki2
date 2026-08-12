import asyncio
import sys

from database.database import async_session_maker
from database.models import Admin, RoleEnum


async def add_admin(user_id: int, role: str, role_level: int):
    async with async_session_maker() as session:
        admin = Admin(
            user_id=user_id,
            role=RoleEnum[role.upper()],
            role_level=role_level,
            added_by=user_id
        )
        session.add(admin)
        await session.commit()
        print(f"✅ Администратор добавлен: user_id={user_id}, role={role}, level={role_level}")


async def main():
    if len(sys.argv) < 4:
        print("Использование: python add_admin.py <user_id> <role> <role_level>")
        print("Роли: ADMIN=1, SPEC_ADMIN=2, SENIOR_ADMIN=3, TECH_ADMIN=4, CO_OWNER=5, OWNER=5")
        print("Баны и статистика (/ban, /unban, /bans, /stats) доступны с уровня 5.")
        print("Пример: python add_admin.py 123456789 OWNER 5")
        return

    user_id = int(sys.argv[1])
    role = sys.argv[2]
    role_level = int(sys.argv[3])

    await add_admin(user_id, role, role_level)


if __name__ == "__main__":
    asyncio.run(main())
