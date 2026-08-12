from sqlalchemy import select

from database.models import Achievement, UserAchievement, Stats, Inventory, ShopItem, ItemType
from database.database import async_session_maker


class AchievementService:
    async def check_and_award_achievements(self, user_id: int) -> list[dict]:
        """Перевіряє та видає досягнення користувачу"""
        async with async_session_maker() as session:
            stats_result = await session.execute(
                select(Stats).where(Stats.user_id == user_id)
            )
            stats = stats_result.scalar_one_or_none()

            if not stats:
                return []

            achievements_result = await session.execute(select(Achievement))
            all_achievements = achievements_result.scalars().all()

            user_achievements_result = await session.execute(
                select(UserAchievement.achievement_id).where(
                    UserAchievement.user_id == user_id
                )
            )
            unlocked_ids = {row[0] for row in user_achievements_result.fetchall()}

            newly_unlocked = []

            for achievement in all_achievements:
                if achievement.id in unlocked_ids:
                    continue

                if await self._check_achievement_condition(
                    session, user_id, stats, achievement
                ):
                    user_achievement = UserAchievement(
                        user_id=user_id,
                        achievement_id=achievement.id
                    )
                    session.add(user_achievement)
                    newly_unlocked.append({
                        "id": achievement.id,
                        "name": achievement.name,
                        "description": achievement.description
                    })

            if newly_unlocked:
                await session.commit()

            return newly_unlocked

    async def _check_achievement_condition(
        self, session, user_id: int, stats: Stats, achievement: Achievement
    ) -> bool:
        """Перевіряє умову для конкретного досягнення"""
        metric_value = 0

        if achievement.metric_type == "login_streak":
            metric_value = stats.login_streak
        elif achievement.metric_type == "games_played":
            metric_value = stats.games_played
        elif achievement.metric_type == "games_won":
            metric_value = stats.games_won
        elif achievement.metric_type == "win_streak":
            metric_value = stats.win_streak
        elif achievement.metric_type == "feedings":
            metric_value = stats.feedings
        elif achievement.metric_type == "baths":
            metric_value = stats.baths
        elif achievement.metric_type == "sleeps":
            metric_value = stats.sleeps
        elif achievement.metric_type == "currency":
            metric_value = stats.currency
        elif achievement.metric_type == "items_bought":
            result = await session.execute(
                select(Inventory).where(Inventory.user_id == user_id)
            )
            metric_value = len(result.scalars().all())
        elif achievement.metric_type == "backgrounds_bought":
            result = await session.execute(
                select(Inventory, ShopItem)
                .join(ShopItem, Inventory.item_id == ShopItem.id)
                .where(
                    Inventory.user_id == user_id,
                    ShopItem.item_type == ItemType.BACKGROUND
                )
            )
            metric_value = len(result.all())

        return metric_value >= achievement.threshold

    async def get_user_achievements(self, user_id: int) -> list[dict]:
        """Отримує список розблокованих досягнень користувача"""
        async with async_session_maker() as session:
            result = await session.execute(
                select(Achievement, UserAchievement)
                .join(UserAchievement, Achievement.id == UserAchievement.achievement_id)
                .where(UserAchievement.user_id == user_id)
            )
            achievements = result.all()

            return [
                {
                    "id": achievement.id,
                    "name": achievement.name,
                    "description": achievement.description,
                    "unlocked_at": user_achievement.unlocked_at.isoformat()
                }
                for achievement, user_achievement in achievements
            ]
