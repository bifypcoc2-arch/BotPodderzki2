import json
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import UserPet, Stats, GameState
from database.database import async_session_maker
from services.word_service import word_service


logger = logging.getLogger(__name__)


class GameService:
    # Кости
    DICE_REWARD = 5
    DICE_COOLDOWN_SECONDS = 60
    DICE_SIDES = 6

    # Сила питомца даёт шанс перебросить проигранный кубик один раз.
    LUCK_PER_STRENGTH = 0.005
    MAX_LUCK = 0.5

    # Шёпот цифр
    NUMBER_MAX = 10
    NUMBER_EXACT_REWARD = 15
    NUMBER_CLOSE_REWARD = 5
    NUMBER_COOLDOWN_SECONDS = 60

    # Вордли
    WORD_LENGTH = 5
    WORDLE_MAX_ATTEMPTS = 6
    WORDLE_REWARD = 30
    WORDLE_COOLDOWN_SECONDS = 6 * 60 * 60

    # ------------------------------------------------------------------ Кости

    async def play_dice(self, user_id: int) -> dict:
        """Оба кубика бросаются честно, у кого больше — тот и выиграл."""
        async with async_session_maker() as session:
            pet = await self._get_pet(session, user_id)

            if not pet:
                return {'success': False, 'message': 'Питомец не найден'}

            state = await self._get_game_state(session, user_id)

            cooldown_left = self._cooldown_left(
                state.last_dice_at, self.DICE_COOLDOWN_SECONDS
            )
            if cooldown_left:
                return {
                    'success': False,
                    'message': f'Кубики отдыхают. Подожди {cooldown_left} сек',
                    'cooldown_left': cooldown_left
                }

            player_roll = random.randint(1, self.DICE_SIDES)
            bot_roll = random.randint(1, self.DICE_SIDES)
            rerolled = False

            # Проиграл — сильный питомец может выпросить один переброс.
            if player_roll < bot_roll and random.random() < self._luck(pet.strength):
                player_roll = random.randint(1, self.DICE_SIDES)
                rerolled = True

            won = player_roll > bot_roll
            draw = player_roll == bot_roll
            reward = self.DICE_REWARD if won else 0

            stats = await self._get_stats(session, user_id)
            stats.games_played += 1
            stats.currency += reward

            if won:
                stats.games_won += 1
                stats.win_streak += 1
            elif not draw:
                # Ничья серию не ломает — игрок не проиграл.
                stats.win_streak = 0

            state.last_dice_at = datetime.utcnow()

            await session.commit()

            return {
                'success': True,
                'won': won,
                'draw': draw,
                'player_roll': player_roll,
                'bot_roll': bot_roll,
                'rerolled': rerolled,
                'reward': reward,
                'currency': stats.currency,
                'win_streak': stats.win_streak,
                'cooldown': self.DICE_COOLDOWN_SECONDS
            }

    # ------------------------------------------------------------ Шёпот цифр

    async def play_number_whisper(self, user_id: int, guess: int) -> dict:
        """Угадай число от 1 до 10. Промах на единицу тоже что-то даёт."""
        if guess < 1 or guess > self.NUMBER_MAX:
            return {
                'success': False,
                'message': f'Число должно быть от 1 до {self.NUMBER_MAX}'
            }

        async with async_session_maker() as session:
            pet = await self._get_pet(session, user_id)

            if not pet:
                return {'success': False, 'message': 'Питомец не найден'}

            state = await self._get_game_state(session, user_id)

            cooldown_left = self._cooldown_left(
                state.last_number_at, self.NUMBER_COOLDOWN_SECONDS
            )
            if cooldown_left:
                return {
                    'success': False,
                    'message': f'Шёпот стих. Подожди {cooldown_left} сек',
                    'cooldown_left': cooldown_left
                }

            secret_number = random.randint(1, self.NUMBER_MAX)
            distance = abs(guess - secret_number)

            if distance == 0:
                reward = self.NUMBER_EXACT_REWARD
            elif distance == 1:
                reward = self.NUMBER_CLOSE_REWARD
            else:
                reward = 0

            won = distance == 0

            stats = await self._get_stats(session, user_id)
            stats.games_played += 1
            stats.currency += reward

            if won:
                stats.games_won += 1
                stats.win_streak += 1
            else:
                stats.win_streak = 0

            state.last_number_at = datetime.utcnow()

            await session.commit()

            return {
                'success': True,
                'won': won,
                'secret_number': secret_number,
                'guess': guess,
                'distance': distance,
                'reward': reward,
                'currency': stats.currency,
                'win_streak': stats.win_streak,
                'cooldown': self.NUMBER_COOLDOWN_SECONDS
            }

    # ----------------------------------------------------------------- Вордли

    async def get_wordle_state(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            state = await self._get_game_state(session, user_id)
            await session.commit()

            return self._wordle_snapshot(state)

    async def start_wordle(self, user_id: int) -> dict:
        async with async_session_maker() as session:
            state = await self._get_game_state(session, user_id)

            # Партия уже идёт — просто возвращаем её, а не загадываем новое слово.
            # Иначе повторный вход в мини-приложение сбрасывал бы прогресс.
            if self._wordle_active(state):
                await session.commit()
                return self._wordle_snapshot(state)

            cooldown_left = self._cooldown_left(
                state.wordle_finished_at, self.WORDLE_COOLDOWN_SECONDS
            )
            if cooldown_left:
                await session.commit()
                snapshot = self._wordle_snapshot(state)
                snapshot['success'] = False
                snapshot['message'] = 'Новое слово пока не готово'
                return snapshot

            state.wordle_word = word_service.random_answer()
            state.wordle_attempts = '[]'
            state.wordle_started_at = datetime.utcnow()
            state.wordle_finished_at = None
            state.wordle_won = False

            await session.commit()

            return self._wordle_snapshot(state)

    async def guess_wordle(self, user_id: int, guess: str) -> dict:
        guess = word_service.normalize(guess)

        if not word_service.is_valid_shape(guess):
            return {
                'success': False,
                'message': 'Нужно слово из 5 русских букв'
            }

        # Проверка по словарю до открытия сессии: она может уйти в сеть
        # при первом вызове, а держать соединение с БД всё это время не нужно.
        if not await word_service.is_known_word(guess):
            return {
                'success': False,
                'message': 'Такого слова нет в словаре',
                'unknown_word': True
            }

        async with async_session_maker() as session:
            state = await self._get_game_state(session, user_id)

            if not self._wordle_active(state):
                await session.commit()
                snapshot = self._wordle_snapshot(state)
                snapshot['success'] = False
                snapshot['message'] = 'Сначала начни новую партию'
                return snapshot

            attempts = self._attempts(state)
            attempts.append(guess)
            state.wordle_attempts = json.dumps(attempts, ensure_ascii=False)

            won = guess == state.wordle_word
            finished = won or len(attempts) >= self.WORDLE_MAX_ATTEMPTS
            reward = 0

            if finished:
                state.wordle_finished_at = datetime.utcnow()
                state.wordle_won = won

                stats = await self._get_stats(session, user_id)
                stats.games_played += 1

                if won:
                    reward = self.WORDLE_REWARD
                    stats.currency += reward
                    stats.games_won += 1
                    stats.win_streak += 1
                else:
                    stats.win_streak = 0

            await session.commit()

            snapshot = self._wordle_snapshot(state)
            snapshot['reward'] = reward
            return snapshot

    # ------------------------------------------------------------ Вспомогательное

    def _wordle_snapshot(self, state: GameState) -> dict:
        """Состояние партии для клиента. Загаданное слово отдаём
        только когда партия завершена."""
        attempts = self._attempts(state)
        word = state.wordle_word or ''

        rows = [
            {'word': attempt, 'result': self._match(attempt, word)}
            for attempt in attempts
        ]

        active = self._wordle_active(state)
        cooldown_left = 0 if active else self._cooldown_left(
            state.wordle_finished_at, self.WORDLE_COOLDOWN_SECONDS
        )

        if active:
            status = 'active'
        elif cooldown_left:
            status = 'cooldown'
        else:
            status = 'idle'

        snapshot = {
            'success': True,
            'status': status,
            'attempts': rows,
            'attempts_left': max(0, self.WORDLE_MAX_ATTEMPTS - len(rows)),
            'max_attempts': self.WORDLE_MAX_ATTEMPTS,
            'word_length': self.WORD_LENGTH,
            'reward': self.WORDLE_REWARD,
            'cooldown_left': cooldown_left,
            'won': bool(state.wordle_won)
        }

        if not active and state.wordle_word:
            snapshot['word'] = state.wordle_word

        return snapshot

    def _wordle_active(self, state: GameState) -> bool:
        return bool(state.wordle_word) and state.wordle_finished_at is None

    def _attempts(self, state: GameState) -> list[str]:
        try:
            attempts = json.loads(state.wordle_attempts or '[]')
        except (TypeError, ValueError):
            logger.warning('Битые попытки вордли у %s', state.user_id)
            return []

        if not isinstance(attempts, list):
            return []

        return [str(item) for item in attempts]

    def _match(self, guess: str, word: str) -> list[str]:
        """Раскраска попытки с учётом повторных букв.

        Если буква в слове одна, а в попытке две, жёлтой должна стать только одна.
        """
        if len(word) != len(guess):
            return ['absent'] * len(guess)

        result = ['absent'] * len(guess)
        remaining: dict[str, int] = {}

        for index, letter in enumerate(word):
            if guess[index] == letter:
                result[index] = 'correct'
            else:
                remaining[letter] = remaining.get(letter, 0) + 1

        for index, letter in enumerate(guess):
            if result[index] == 'correct':
                continue

            if remaining.get(letter, 0) > 0:
                result[index] = 'present'
                remaining[letter] -= 1

        return result

    def _luck(self, strength: int) -> float:
        return min(self.MAX_LUCK, strength * self.LUCK_PER_STRENGTH)

    def _cooldown_left(self, last_time: datetime | None, cooldown: int) -> int:
        if not last_time:
            return 0

        ready_at = last_time + timedelta(seconds=cooldown)
        left = (ready_at - datetime.utcnow()).total_seconds()

        return max(0, int(left))

    async def _get_pet(self, session, user_id: int) -> UserPet | None:
        result = await session.execute(
            select(UserPet).where(UserPet.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _get_stats(self, session, user_id: int) -> Stats:
        result = await session.execute(
            select(Stats).where(Stats.user_id == user_id)
        )
        stats = result.scalar_one_or_none()

        if not stats:
            stats = Stats(user_id=user_id)
            session.add(stats)

        return stats

    async def _get_game_state(self, session, user_id: int) -> GameState:
        result = await session.execute(
            select(GameState).where(GameState.user_id == user_id)
        )
        state = result.scalar_one_or_none()

        if not state:
            state = GameState(user_id=user_id, wordle_attempts='[]')
            session.add(state)
            await session.flush()

        return state
