from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ShopItem, Inventory, Stats, UserPet, ItemType
from database.database import async_session_maker


class ShopService:
    async def buy_item(self, user_id: int, item_id: int) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(ShopItem).where(ShopItem.id == item_id)
            )
            shop_item = result.scalar_one_or_none()

            if not shop_item:
                return {"success": False, "message": "Предмет не найден"}

            stats_result = await session.execute(
                select(Stats).where(Stats.user_id == user_id)
            )
            stats = stats_result.scalar_one_or_none()

            if not stats:
                stats = Stats(user_id=user_id)
                session.add(stats)

            if stats.currency < shop_item.price:
                return {"success": False, "message": "Недостаточно монет"}

            if not shop_item.is_consumable:
                inv_result = await session.execute(
                    select(Inventory).where(
                        Inventory.user_id == user_id,
                        Inventory.item_id == item_id
                    )
                )
                existing = inv_result.scalar_one_or_none()

                if existing:
                    return {"success": False, "message": "Уже куплено"}

            stats.currency -= shop_item.price

            inv_result = await session.execute(
                select(Inventory).where(
                    Inventory.user_id == user_id,
                    Inventory.item_id == item_id
                )
            )
            inventory = inv_result.scalar_one_or_none()

            if inventory:
                inventory.quantity += 1
            else:
                inventory = Inventory(
                    user_id=user_id,
                    item_id=item_id,
                    quantity=1
                )
                session.add(inventory)

            await session.commit()

            return {
                "success": True,
                "remaining_currency": stats.currency,
                "item_name": shop_item.name
            }

    async def use_item(self, user_id: int, inventory_id: int) -> dict:
        async with async_session_maker() as session:
            inv_result = await session.execute(
                select(Inventory, ShopItem)
                .join(ShopItem, Inventory.item_id == ShopItem.id)
                .where(Inventory.id == inventory_id, Inventory.user_id == user_id)
            )
            result = inv_result.first()

            if not result:
                return {"success": False, "message": "Предмет не найден"}

            inventory, shop_item = result

            if inventory.quantity <= 0:
                return {"success": False, "message": "Предмет закончился"}

            if shop_item.item_type == ItemType.FOOD:
                pet_result = await session.execute(
                    select(UserPet).where(UserPet.user_id == user_id)
                )
                pet = pet_result.scalar_one_or_none()

                if not pet:
                    return {"success": False, "message": "Питомец не найден"}

                if shop_item.effect_type == "hunger":
                    pet.hunger = min(100, pet.hunger + shop_item.effect_value)

                inventory.quantity -= 1
                if inventory.quantity <= 0:
                    await session.delete(inventory)

                await session.commit()

                return {
                    "success": True,
                    "message": f"Использовано: {shop_item.name}",
                    "hunger": pet.hunger
                }

            elif shop_item.item_type == ItemType.TOY:
                pet_result = await session.execute(
                    select(UserPet).where(UserPet.user_id == user_id)
                )
                pet = pet_result.scalar_one_or_none()

                if not pet:
                    return {"success": False, "message": "Питомец не найден"}

                if shop_item.effect_type == "happiness":
                    pet.happiness = min(100, pet.happiness + shop_item.effect_value)

                inventory.quantity -= 1
                if inventory.quantity <= 0:
                    await session.delete(inventory)

                await session.commit()

                return {
                    "success": True,
                    "message": f"Использовано: {shop_item.name}",
                    "happiness": pet.happiness
                }

            else:
                return {"success": False, "message": "Этот предмет нельзя использовать"}

    async def equip_item(self, user_id: int, inventory_id: int) -> dict:
        async with async_session_maker() as session:
            inv_result = await session.execute(
                select(Inventory, ShopItem)
                .join(ShopItem, Inventory.item_id == ShopItem.id)
                .where(Inventory.id == inventory_id, Inventory.user_id == user_id)
            )
            result = inv_result.first()

            if not result:
                return {"success": False, "message": "Предмет не найден"}

            inventory, shop_item = result

            if shop_item.item_type not in [ItemType.ACCESSORY, ItemType.BACKGROUND]:
                return {"success": False, "message": "Этот предмет нельзя экипировать"}

            all_inv_result = await session.execute(
                select(Inventory, ShopItem)
                .join(ShopItem, Inventory.item_id == ShopItem.id)
                .where(
                    Inventory.user_id == user_id,
                    ShopItem.item_type == shop_item.item_type,
                    Inventory.is_equipped == True
                )
            )
            equipped_items = all_inv_result.all()

            for equipped_inv, _ in equipped_items:
                equipped_inv.is_equipped = False

            inventory.is_equipped = True

            await session.commit()

            return {
                "success": True,
                "message": f"Экипировано: {shop_item.name}"
            }
