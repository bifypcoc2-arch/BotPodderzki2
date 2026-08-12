from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import UserPet, User, Stats, PetStage, PetType
from database.database import async_session_maker


class PetService:
    XP_THRESHOLDS = {
        PetStage.CONCEPTION: 10,
        PetStage.EGG: 50,
        PetStage.BABY: 150,
        PetStage.TEEN: 300,
        PetStage.ADULT: float('inf')
    }

    PARAM_DECAY_RATE = 5

    # Энергия убывает медленнее остального: полный запас тратится примерно за сутки с небольшим.
    ENERGY_DECAY_RATE = 3

    PARAM_DECAY_INTERVAL = 3600

    FEED_COOLDOWN = 300
    PLAY_COOLDOWN = 600
    WASH_COOLDOWN = 900
    SLEEP_COOLDOWN = 1800
    TRAIN_COOLDOWN = 1200

    def _apply_decay(self, pet: UserPet) -> bool:
        """Посчитать деградацию параметров за прошедшее время.

        Вызывается при каждом обращении к питомцу, поэтому значения всегда
        актуальны на момент действия, а не на момент последнего прогона
        фоновой задачи. Возвращает True, если что-то изменилось.
        """
        if not pet.last_updated:
            pet.last_updated = datetime.utcnow()
            return True

        elapsed = (datetime.utcnow() - pet.last_updated).total_seconds()
        cycles = int(elapsed // self.PARAM_DECAY_INTERVAL)

        if cycles <= 0:
            return False

        pet.hunger = max(0, pet.hunger - self.PARAM_DECAY_RATE * cycles)
        pet.happiness = max(0, pet.happiness - self.PARAM_DECAY_RATE * cycles)
        pet.hygiene = max(0, pet.hygiene - self.PARAM_DECAY_RATE * cycles)
        pet.discipline = max(0, pet.discipline - self.PARAM_DECAY_RATE * cycles)

        # Главное исправление: раньше энергии здесь не было, поэтому она вечно
        # оставалась 100 и сон всегда отвечал "питомец не устал".
        pet.energy = max(0, pet.energy - self.ENERGY_DECAY_RATE * cycles)

        # Сдвигаем на целое число циклов, а не на "сейчас": иначе остаток
        # минут сгорает при каждом пересчёте и деградация идёт медленнее заявленной.
        pet.last_updated = pet.last_updated + timedelta(
            seconds=cycles * self.PARAM_DECAY_INTERVAL
        )

        return True

    async def get_or_create_pet(self, user_id: int) -> UserPet:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                await self._get_or_create_user(session, user_id)
                pet = UserPet(user_id=user_id)
                session.add(pet)
                await session.commit()
                await session.refresh(pet)
                return pet

            if self._apply_decay(pet):
                await session.commit()
                await session.refresh(pet)

            return pet

    async def update_parameters(self, user_id: int) -> Optional[UserPet]:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return None

            if self._apply_decay(pet):
                await session.commit()
                await session.refresh(pet)

            return pet

    async def feed_pet(self, user_id: int, food_effect: int = 20) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return {"success": False, "message": "Питомец не найден"}

            self._apply_decay(pet)

            if pet.last_feed_time:
                time_since_last = (datetime.utcnow() - pet.last_feed_time).total_seconds()
                if time_since_last < self.FEED_COOLDOWN:
                    remaining = int(self.FEED_COOLDOWN - time_since_last)
                    return {"success": False, "message": f"Кулдаун. Осталось {remaining} сек"}

            pet.hunger = min(100, pet.hunger + food_effect)
            pet.xp += 5
            pet.last_feed_time = datetime.utcnow()

            stats = await self._get_stats(session, user_id)
            stats.feedings += 1

            await self._check_stage_upgrade(pet)
            await session.commit()

            return {"success": True, "hunger": pet.hunger, "xp": pet.xp}

    async def play_with_pet(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return {"success": False, "message": "Питомец не найден"}

            self._apply_decay(pet)

            if pet.last_play_time:
                time_since_last = (datetime.utcnow() - pet.last_play_time).total_seconds()
                if time_since_last < self.PLAY_COOLDOWN:
                    remaining = int(self.PLAY_COOLDOWN - time_since_last)
                    return {"success": False, "message": f"Кулдаун. Осталось {remaining} сек"}

            pet.happiness = min(100, pet.happiness + 15)
            pet.xp += 3
            pet.last_play_time = datetime.utcnow()

            await self._check_stage_upgrade(pet)
            await session.commit()

            return {"success": True, "happiness": pet.happiness, "xp": pet.xp}

    async def wash_pet(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return {"success": False, "message": "Питомец не найден"}

            self._apply_decay(pet)

            if pet.last_wash_time:
                time_since_last = (datetime.utcnow() - pet.last_wash_time).total_seconds()
                if time_since_last < self.WASH_COOLDOWN:
                    remaining = int(self.WASH_COOLDOWN - time_since_last)
                    return {"success": False, "message": f"Кулдаун. Осталось {remaining} сек"}

            pet.hygiene = min(100, pet.hygiene + 25)
            pet.xp += 4
            pet.last_wash_time = datetime.utcnow()

            stats = await self._get_stats(session, user_id)
            stats.baths += 1

            await self._check_stage_upgrade(pet)
            await session.commit()

            return {"success": True, "hygiene": pet.hygiene, "xp": pet.xp}

    async def sleep_pet(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return {"success": False, "message": "Питомец не найден"}

            # Сначала деградация, потом проверка "не устал": иначе сравниваем
            # с устаревшим значением энергии.
            self._apply_decay(pet)

            if pet.energy >= 100:
                return {"success": False, "message": "Питомец не устал"}

            if pet.last_sleep_time:
                time_since_last = (datetime.utcnow() - pet.last_sleep_time).total_seconds()
                if time_since_last < self.SLEEP_COOLDOWN:
                    remaining = int(self.SLEEP_COOLDOWN - time_since_last)
                    return {"success": False, "message": f"Кулдаун. Осталось {remaining} сек"}

            pet.energy = min(100, pet.energy + 30)
            pet.xp += 3
            pet.last_sleep_time = datetime.utcnow()

            stats = await self._get_stats(session, user_id)
            stats.sleeps += 1

            await self._check_stage_upgrade(pet)
            await session.commit()

            return {"success": True, "energy": pet.energy, "xp": pet.xp}

    async def train_pet(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            result = await session.execute(
                select(UserPet).where(UserPet.user_id == user_id)
            )
            pet = result.scalar_one_or_none()

            if not pet:
                return {"success": False, "message": "Питомец не найден"}

            self._apply_decay(pet)

            if pet.energy < 20:
                return {"success": False, "message": "Недостаточно энергии"}

            if pet.last_train_time:
                time_since_last = (datetime.utcnow() - pet.last_train_time).total_seconds()
                if time_since_last < self.TRAIN_COOLDOWN:
                    remaining = int(self.TRAIN_COOLDOWN - time_since_last)
                    return {"success": False, "message": f"Кулдаун. Осталось {remaining} сек"}

            pet.energy = max(0, pet.energy - 20)
            pet.discipline = min(100, pet.discipline + 10)
            pet.strength = min(100, pet.strength + 5)
            pet.xp += 8
            pet.last_train_time = datetime.utcnow()

            stats = await self._get_stats(session, user_id)
            stats.trainings += 1

            await self._check_stage_upgrade(pet)
            await session.commit()

            return {
                "success": True,
                "energy": pet.energy,
                "discipline": pet.discipline,
                "strength": pet.strength,
                "xp": pet.xp
            }

    async def _check_stage_upgrade(self, pet: UserPet):
        if pet.stage == PetStage.ADULT:
            return

        all_params_good = all([
            pet.hunger >= 60,
            pet.happiness >= 60,
            pet.hygiene >= 60,
            pet.energy >= 60,
            pet.discipline >= 60
        ])

        if not all_params_good:
            return

        stages = [PetStage.CONCEPTION, PetStage.EGG, PetStage.BABY, PetStage.TEEN, PetStage.ADULT]
        current_index = stages.index(pet.stage)

        if current_index < len(stages) - 1:
            next_stage = stages[current_index + 1]
            threshold = self.XP_THRESHOLDS[pet.stage]

            if pet.xp >= threshold:
                pet.stage = next_stage

                if pet.stage == PetStage.BABY and not pet.pet_type:
                    import random
                    pet.pet_type = random.choice(list(PetType))

    async def _get_or_create_user(self, session: AsyncSession, user_id: int) -> User:
        result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            user = User(user_id=user_id)
            session.add(user)

        return user

    async def _get_stats(self, session: AsyncSession, user_id: int) -> Stats:
        result = await session.execute(
            select(Stats).where(Stats.user_id == user_id)
        )
        stats = result.scalar_one_or_none()

        if not stats:
            stats = Stats(user_id=user_id)
            session.add(stats)

        return stats
