import asyncio

from database.database import init_db
from database.models import ShopItem, ItemType, Achievement
from database.database import async_session_maker


async def seed_shop_items():
    async with async_session_maker() as session:
        food_items = [
            ShopItem(name="Призрачное мясо", item_type=ItemType.FOOD, price=50, effect_type="hunger", effect_value=25),
            ShopItem(name="Лунные ягоды", item_type=ItemType.FOOD, price=30, effect_type="hunger", effect_value=15),
            ShopItem(name="Туманный суп", item_type=ItemType.FOOD, price=40, effect_type="hunger", effect_value=20),
            ShopItem(name="Эфирный нектар", item_type=ItemType.FOOD, price=60, effect_type="hunger", effect_value=30),
            ShopItem(name="Теневой десерт", item_type=ItemType.FOOD, price=45, effect_type="hunger", effect_value=22),
            ShopItem(name="Звёздный корм", item_type=ItemType.FOOD, price=70, effect_type="hunger", effect_value=35),
        ]

        toy_items = [
            ShopItem(name="Светящийся мяч", item_type=ItemType.TOY, price=80, effect_type="happiness", effect_value=20),
            ShopItem(name="Призрачная мышка", item_type=ItemType.TOY, price=65, effect_type="happiness", effect_value=15),
            ShopItem(name="Магический волчок", item_type=ItemType.TOY, price=90, effect_type="happiness", effect_value=25),
            ShopItem(name="Теневой клубок", item_type=ItemType.TOY, price=75, effect_type="happiness", effect_value=18),
            ShopItem(name="Ловец снов", item_type=ItemType.TOY, price=100, effect_type="happiness", effect_value=30),
        ]

        accessory_items = [
            ShopItem(name="Призрачный ошейник", item_type=ItemType.ACCESSORY, price=150, is_consumable=False),
            ShopItem(name="Плащ невидимости", item_type=ItemType.ACCESSORY, price=300, is_consumable=False),
            ShopItem(name="Лунный кулон", item_type=ItemType.ACCESSORY, price=200, is_consumable=False),
            ShopItem(name="Теневой венок", item_type=ItemType.ACCESSORY, price=180, is_consumable=False),
            ShopItem(name="Плащ теней", item_type=ItemType.ACCESSORY, price=250, is_consumable=False),
            ShopItem(name="Призрачный бант", item_type=ItemType.ACCESSORY, price=120, is_consumable=False),
            ShopItem(name="Ожерелье", item_type=ItemType.ACCESSORY, price=220, is_consumable=False),
            ShopItem(name="Корона ночи", item_type=ItemType.ACCESSORY, price=500, is_consumable=False),
        ]

        background_items = [
            ShopItem(name="Заколдованный лес", item_type=ItemType.BACKGROUND, price=200, is_consumable=False),
            ShopItem(name="Призрачный замок", item_type=ItemType.BACKGROUND, price=300, is_consumable=False),
            ShopItem(name="Лунная поляна", item_type=ItemType.BACKGROUND, price=250, is_consumable=False),
            ShopItem(name="Туманное болото", item_type=ItemType.BACKGROUND, price=180, is_consumable=False),
            ShopItem(name="Звёздная бездна", item_type=ItemType.BACKGROUND, price=400, is_consumable=False),
        ]

        all_items = food_items + toy_items + accessory_items + background_items
        session.add_all(all_items)
        await session.commit()
        print(f"✅ Добавлено {len(all_items)} предметов в магазин")


async def seed_achievements():
    async with async_session_maker() as session:
        achievements = [
            Achievement(name="Новичок", description="Первый день ухода за питомцем", metric_type="login_streak", threshold=1),
            Achievement(name="Друг питомца", description="3 дня заботы", metric_type="login_streak", threshold=3),
            Achievement(name="Сердце питомца", description="7 дней заботы", metric_type="login_streak", threshold=7),
            Achievement(name="Легенда", description="14 дней заботы", metric_type="login_streak", threshold=14),
            Achievement(name="Вечная связь", description="30 дней заботы", metric_type="login_streak", threshold=30),
            Achievement(name="Азартный игрок", description="Сыграть 10 игр", metric_type="games_played", threshold=10),
            Achievement(name="Король кубиков", description="Выиграть 20 игр", metric_type="games_won", threshold=20),
            Achievement(name="Гадалка", description="Серия из 5 побед", metric_type="win_streak", threshold=5),
            Achievement(name="Заботливый хозяин", description="Покормить 50 раз", metric_type="feedings", threshold=50),
            Achievement(name="Чистюля", description="Искупать 30 раз", metric_type="baths", threshold=30),
            Achievement(name="Соня", description="Уложить спать 40 раз", metric_type="sleeps", threshold=40),
            Achievement(name="Призрачный капитал", description="Накопить 1000 монет", metric_type="currency", threshold=1000),
            Achievement(name="Тень богатства", description="Накопить 5000 монет", metric_type="currency", threshold=5000),
            Achievement(name="Коллекционер", description="Купить 10 предметов", metric_type="items_bought", threshold=10),
            Achievement(name="Фоновый маг", description="Купить все фоны", metric_type="backgrounds_bought", threshold=5),
            Achievement(name="Преданный дух", description="7 дней входов подряд", metric_type="login_streak", threshold=7),
            Achievement(name="Призрачный страж", description="14 дней входов подряд", metric_type="login_streak", threshold=14),
            Achievement(name="Повелитель духов", description="30 дней входов подряд", metric_type="login_streak", threshold=30),
        ]

        session.add_all(achievements)
        await session.commit()
        print(f"✅ Добавлено {len(achievements)} достижений")


async def main():
    print("Инициализация базы данных...")
    await init_db()
    print("✅ База данных инициализирована")

    print("\nЗаполнение магазина...")
    await seed_shop_items()

    print("\nДобавление достижений...")
    await seed_achievements()

    print("\n✅ Инициализация завершена!")
    print("\nТеперь добавьте первого администратора:")
    print("python add_admin.py <your_telegram_id> OWNER 5")


if __name__ == "__main__":
    asyncio.run(main())
