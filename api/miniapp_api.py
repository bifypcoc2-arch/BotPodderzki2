import logging

from aiohttp import web
from sqlalchemy import select

from database.models import Stats, Inventory, ShopItem
from database.database import async_session_maker
from services.pet_service import PetService
from services.shop_service import ShopService
from services.game_service import GameService
from services.achievement_service import AchievementService


logger = logging.getLogger(__name__)

# Порог параметров, без которого питомец не перейдёт на следующую стадию.
# Должен совпадать с проверкой в PetService._check_stage_upgrade.
GROWTH_PARAM_MINIMUM = 60


def _bad_request(message: str) -> web.Response:
    return web.json_response({'success': False, 'message': message}, status=400)


async def _json_body(request: web.Request) -> dict | None:
    """Битое тело запроса раньше роняло обработчик в 500."""
    try:
        data = await request.json()
    except Exception:
        return None

    return data if isinstance(data, dict) else None


def _int_field(data: dict, name: str) -> int | None:
    try:
        return int(data[name])
    except (KeyError, TypeError, ValueError):
        return None


class MiniAppAPI:
    """user_id всегда берётся из request["user_id"], куда его кладёт
    telegram_auth_middleware после проверки подписи. Значения user_id
    из тела или query-строки игнорируются: им нельзя доверять."""

    def __init__(self):
        self.pet_service = PetService()
        self.shop_service = ShopService()
        self.game_service = GameService()
        self.achievement_service = AchievementService()

    async def get_pet_state(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        pet = await self.pet_service.update_parameters(user_id)

        if not pet:
            pet = await self.pet_service.get_or_create_pet(user_id)

        threshold = self.pet_service.XP_THRESHOLDS.get(pet.stage)
        # У взрослой стадии порог равен бесконечности — в JSON её не отдать,
        # да и расти дальше некуда, поэтому отдаём null.
        if threshold is None or threshold == float('inf'):
            next_stage_xp = None
        else:
            next_stage_xp = int(threshold)

        growth_ready = all([
            pet.hunger >= GROWTH_PARAM_MINIMUM,
            pet.happiness >= GROWTH_PARAM_MINIMUM,
            pet.hygiene >= GROWTH_PARAM_MINIMUM,
            pet.energy >= GROWTH_PARAM_MINIMUM,
            pet.discipline >= GROWTH_PARAM_MINIMUM
        ])

        return web.json_response({
            'pet_type': pet.pet_type.value if pet.pet_type else None,
            'stage': pet.stage.value,
            'xp': pet.xp,
            'next_stage_xp': next_stage_xp,
            'growth_ready': growth_ready,
            'growth_param_minimum': GROWTH_PARAM_MINIMUM,
            'hunger': pet.hunger,
            'happiness': pet.happiness,
            'hygiene': pet.hygiene,
            'energy': pet.energy,
            'discipline': pet.discipline,
            'strength': pet.strength
        })

    async def perform_action(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        action = data.get('action')

        if action == 'feed':
            result = await self.pet_service.feed_pet(user_id)
        elif action == 'play':
            result = await self.pet_service.play_with_pet(user_id)
        elif action == 'wash':
            result = await self.pet_service.wash_pet(user_id)
        elif action == 'sleep':
            result = await self.pet_service.sleep_pet(user_id)
        elif action == 'train':
            result = await self.pet_service.train_pet(user_id)
        else:
            return _bad_request('Неизвестное действие')

        return web.json_response(result)

    async def get_stats(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        async with async_session_maker() as session:
            result = await session.execute(
                select(Stats).where(Stats.user_id == user_id)
            )
            stats = result.scalars().first()

            if not stats:
                return web.json_response({
                    'messages_sent': 0,
                    'games_played': 0,
                    'games_won': 0,
                    'win_streak': 0,
                    'feedings': 0,
                    'baths': 0,
                    'sleeps': 0,
                    'trainings': 0,
                    'login_streak': 0,
                    'currency': 0
                })

            return web.json_response({
                'messages_sent': stats.messages_sent,
                'games_played': stats.games_played,
                'games_won': stats.games_won,
                'win_streak': stats.win_streak,
                'feedings': stats.feedings,
                'baths': stats.baths,
                'sleeps': stats.sleeps,
                'trainings': stats.trainings,
                'login_streak': stats.login_streak,
                'currency': stats.currency
            })

    async def get_inventory(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        async with async_session_maker() as session:
            result = await session.execute(
                select(Inventory, ShopItem)
                .join(ShopItem, Inventory.item_id == ShopItem.id)
                .where(Inventory.user_id == user_id)
            )
            items = result.all()

            inventory_data = []
            for inv, shop_item in items:
                inventory_data.append({
                    'id': inv.id,
                    'item_id': shop_item.id,
                    'name': shop_item.name,
                    'type': shop_item.item_type.value,
                    'quantity': inv.quantity,
                    'is_equipped': inv.is_equipped
                })

            return web.json_response({'items': inventory_data})

    async def get_shop_items(self, request: web.Request) -> web.Response:
        async with async_session_maker() as session:
            result = await session.execute(select(ShopItem))
            items = result.scalars().all()

            shop_data = []
            for item in items:
                shop_data.append({
                    'id': item.id,
                    'name': item.name,
                    'type': item.item_type.value,
                    'price': item.price,
                    'effect_type': item.effect_type,
                    'effect_value': item.effect_value,
                    'is_consumable': item.is_consumable
                })

            return web.json_response({'items': shop_data})

    async def buy_item(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        item_id = _int_field(data, 'item_id')
        if item_id is None:
            return _bad_request('Не указан предмет')

        result = await self.shop_service.buy_item(user_id, item_id)
        return web.json_response(result)

    async def use_item(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        inventory_id = _int_field(data, 'inventory_id')
        if inventory_id is None:
            return _bad_request('Не указан предмет из инвентаря')

        result = await self.shop_service.use_item(user_id, inventory_id)
        return web.json_response(result)

    async def equip_item(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        inventory_id = _int_field(data, 'inventory_id')
        if inventory_id is None:
            return _bad_request('Не указан предмет из инвентаря')

        result = await self.shop_service.equip_item(user_id, inventory_id)
        return web.json_response(result)

    async def play_dice(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        result = await self.game_service.play_dice(user_id)
        return web.json_response(result)

    async def play_number_whisper(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        guess = _int_field(data, 'guess')
        if guess is None:
            return _bad_request('Нужно число')

        result = await self.game_service.play_number_whisper(user_id, guess)
        return web.json_response(result)

    async def get_wordle(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        result = await self.game_service.get_wordle_state(user_id)
        return web.json_response(result)

    async def start_wordle(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        result = await self.game_service.start_wordle(user_id)
        return web.json_response(result)

    async def guess_wordle(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        data = await _json_body(request)
        if data is None:
            return _bad_request('Некорректный запрос')

        guess = data.get('guess')
        if not isinstance(guess, str) or not guess.strip():
            return _bad_request('Нужно слово')

        result = await self.game_service.guess_wordle(user_id, guess)
        return web.json_response(result)

    async def check_achievements(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        newly_unlocked = await self.achievement_service.check_and_award_achievements(user_id)
        return web.json_response({'newly_unlocked': newly_unlocked})

    async def get_achievements(self, request: web.Request) -> web.Response:
        user_id = request['user_id']

        achievements = await self.achievement_service.get_user_achievements(user_id)
        return web.json_response({'achievements': achievements})


def setup_routes(app: web.Application):
    api = MiniAppAPI()

    app.router.add_get('/api/pet', api.get_pet_state)
    app.router.add_post('/api/action', api.perform_action)
    app.router.add_get('/api/stats', api.get_stats)
    app.router.add_get('/api/inventory', api.get_inventory)
    app.router.add_get('/api/shop', api.get_shop_items)
    app.router.add_post('/api/shop/buy', api.buy_item)
    app.router.add_post('/api/shop/use', api.use_item)
    app.router.add_post('/api/shop/equip', api.equip_item)
    app.router.add_post('/api/game/dice', api.play_dice)
    app.router.add_post('/api/game/number-whisper', api.play_number_whisper)
    app.router.add_get('/api/game/wordle', api.get_wordle)
    app.router.add_post('/api/game/wordle/start', api.start_wordle)
    app.router.add_post('/api/game/wordle/guess', api.guess_wordle)
    app.router.add_get('/api/achievements/check', api.check_achievements)
    app.router.add_get('/api/achievements', api.get_achievements)
