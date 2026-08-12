import asyncio
import logging
import random
import re
from pathlib import Path

import aiohttp


logger = logging.getLogger(__name__)

# 27 тысяч русских слов из пяти букв, один файл без ключей и лимитов.
DICTIONARY_URL = (
    'https://raw.githubusercontent.com/mediahope/'
    'Wordle-Russian-Dictionary/main/Russian.txt'
)

# Куда кладём скачанный словарь, чтобы после перезапуска не качать заново.
CACHE_PATH = Path('data') / 'wordle_ru.txt'

DOWNLOAD_TIMEOUT_SECONDS = 20

# Если скачалось меньше — скорее всего пришла страница ошибки, а не словарь.
MIN_DICTIONARY_SIZE = 1000

WORD_PATTERN = re.compile(r'^[а-я]{5}$')

# Слова, которые бот загадывает. Отдельный короткий список нужен потому,
# что в большом словаре полно падежных форм и редкостей вроде «абака»:
# угадывать такое невесело. Здесь только обиходные слова.
ANSWER_WORDS = [
    'акула', 'арбуз', 'банан', 'башня', 'белка', 'берег', 'блины', 'буква',
    'ветка', 'ветер', 'вечер', 'вилка', 'вишня', 'волки', 'ворон', 'время',
    'город', 'грибы', 'гроза', 'груша', 'дупло', 'дятел', 'жираф', 'забор',
    'закат', 'зверь', 'зебра', 'земля', 'знамя', 'зубры', 'игрок', 'икона',
    'кабан', 'какао', 'калач', 'камин', 'канат', 'капля', 'карта', 'кефир',
    'кисть', 'клоун', 'ключи', 'книга', 'кобра', 'ковер', 'козел', 'колос',
    'комар', 'комод', 'конец', 'конус', 'корка', 'кости', 'кошка', 'крест',
    'кроты', 'круги', 'крыша', 'кусты', 'кухня', 'лампа', 'лапки', 'ласты',
    'лента', 'лесок', 'леший', 'ливни', 'лимон', 'листы', 'лодка', 'ложка',
    'лужок', 'лучик', 'магия', 'маска', 'масло', 'мачта', 'метро', 'месяц',
    'мешок', 'миска', 'мишка', 'молот', 'монах', 'мороз', 'мосты', 'мошка',
    'музей', 'мысли', 'мышка', 'нитка', 'номер', 'норка', 'носки', 'ночка',
    'обувь', 'овощи', 'огонь', 'озеро', 'окунь', 'олень', 'опера', 'орехи',
    'осада', 'осень', 'отель', 'палец', 'палка', 'панда', 'папка', 'парта',
    'парус', 'пепел', 'перец', 'песня', 'песок', 'петух', 'печка', 'пилот',
    'пирог', 'плащи', 'плита', 'повар', 'поезд', 'покой', 'полет', 'полка',
    'порог', 'поток', 'почта', 'право', 'пруды', 'птица', 'пудра', 'пульт',
    'пчела', 'пятно', 'радио', 'рамка', 'ранец', 'ремни', 'рифма', 'робот',
    'ролик', 'рояль', 'рубеж', 'ручей', 'ручка', 'рыбак', 'рынок', 'сабля',
    'салат', 'санки', 'сахар', 'свеча', 'север', 'скала', 'слеза', 'слива',
    'слово', 'смола', 'снега', 'сокол', 'сосна', 'спорт', 'старт', 'стена',
    'стиль', 'столб', 'струя', 'судья', 'сумка', 'сурок', 'сучок', 'сушка',
    'сцена', 'тайна', 'танец', 'тапки', 'театр', 'терем', 'тесто', 'тигры',
    'тиски', 'ткань', 'товар', 'топор', 'точка', 'трава', 'треск', 'тропа',
    'труба', 'тулуп', 'туман', 'тунец', 'тучка', 'тыква', 'уголь', 'узоры',
    'уклон', 'улица', 'устье', 'уступ', 'утюги', 'ферма', 'фикус', 'филин',
    'флаги', 'фокус', 'форма', 'фрукт', 'фужер', 'халат', 'хвост', 'холод',
    'хомяк', 'храмы', 'цапля', 'цветы', 'чайка', 'часть', 'чашка', 'червь',
    'черта', 'чехол', 'чижик', 'чулок', 'шалаш', 'шапка', 'шарик', 'шахта',
    'школа', 'шкура', 'шляпа', 'шорты', 'шоссе', 'штора', 'щенок', 'щетка',
    'эклер', 'экран', 'ябеда', 'ягода', 'ягуар', 'якорь'
]


class WordService:
    """Словарь для вордли.

    Загаданные слова берём из короткого встроенного списка,
    а большой скачанный словарь нужен только для одного вопроса:
    существует ли слово, которое ввёл игрок.
    """

    def __init__(self):
        self._known_words: set[str] | None = None
        self._lock = asyncio.Lock()
        self._download_failed = False

    @staticmethod
    def normalize(word: str) -> str:
        """Ё и е считаем одной буквой: иначе игрок угадывает слово,
        но не может попасть в него с обычной клавиатуры."""
        return word.strip().lower().replace('ё', 'е')

    @classmethod
    def is_valid_shape(cls, word: str) -> bool:
        return bool(WORD_PATTERN.match(cls.normalize(word)))

    def random_answer(self) -> str:
        return self.normalize(random.choice(ANSWER_WORDS))

    async def is_known_word(self, word: str) -> bool:
        word = self.normalize(word)

        if word in {self.normalize(item) for item in ANSWER_WORDS}:
            return True

        words = await self._get_dictionary()

        # Словарь не загрузился — не блокируем игру, принимаем любое слово
        # нужной формы. Лучше пустить выдуманное слово, чем сломать игру.
        if not words:
            return True

        return word in words

    async def _get_dictionary(self) -> set[str]:
        if self._known_words is not None:
            return self._known_words

        async with self._lock:
            # Пока ждали блокировку, словарь мог загрузить соседний запрос.
            if self._known_words is not None:
                return self._known_words

            words = self._read_cache()

            if not words and not self._download_failed:
                words = await self._download()

                if words:
                    self._write_cache(words)
                else:
                    # Больше не дёргаем сеть на каждом ходе до перезапуска.
                    self._download_failed = True

            self._known_words = words
            return self._known_words

    def _read_cache(self) -> set[str]:
        if not CACHE_PATH.exists():
            return set()

        try:
            raw = CACHE_PATH.read_text(encoding='utf-8')
        except OSError:
            logger.warning('Не удалось прочитать %s', CACHE_PATH, exc_info=True)
            return set()

        return self._parse(raw)

    def _write_cache(self, words: set[str]) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text('\n'.join(sorted(words)), encoding='utf-8')
        except OSError:
            logger.warning('Не удалось сохранить %s', CACHE_PATH, exc_info=True)

    async def _download(self) -> set[str]:
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(DICTIONARY_URL) as response:
                    if response.status != 200:
                        logger.warning(
                            'Словарь не скачался, код %s', response.status
                        )
                        return set()

                    raw = await response.text()
        except Exception:
            logger.warning('Словарь не скачался', exc_info=True)
            return set()

        words = self._parse(raw)

        if len(words) < MIN_DICTIONARY_SIZE:
            logger.warning('Словарь подозрительно маленький: %s слов', len(words))
            return set()

        logger.info('Словарь вордли загружен: %s слов', len(words))
        return words

    def _parse(self, raw: str) -> set[str]:
        words = set()

        for line in raw.splitlines():
            word = self.normalize(line)

            if WORD_PATTERN.match(word):
                words.add(word)

        return words


word_service = WordService()
