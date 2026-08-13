# BotPodderzki2

Telegram-бот поддержки на **pyTelegramBotAPI (async)** с обращениями через темы форум-группы и Telegram Mini App: питомец, мини-игры, магазин и достижения.

Пользователям: [гайд по возможностям бота](USER_GUIDE.md).

## Возможности

### Поддержка
- Пользователь пишет боту в личку — для обращения создаётся отдельная тема в форум-группе.
- Ответ админа в теме уходит пользователю; переписка анонимна (`services/anonymity.py`).
- Закрытие обращений, назначение ответственного специалиста, баны и статистика.
- Если ответа долго нет, бот предлагает пользователю мини-игры (`QUEUE_WAIT_MINUTES`).

### Админка
- Рассылки с пошаговым сценарием и восстановлением незавершённых после перезапуска (`services/recovery.py`).
- Модерация общего чата и учёт активности (`services/chat_moderation_service.py`).
- Роли и уровни доступа (`filters/role_filter.py`, `services/admin_service.py`).

### Mini App
- Виртуальный питомец: голод, счастье, гигиена, энергия, дисциплина, сила, стадии роста и XP.
- Мини-игры: кости, «шёпот числа», Wordle.
- Магазин, инвентарь, экипировка предметов, внутренняя валюта, достижения.

### Надёжность
- Состояние диалогов хранится на диске (`STATE_FILE`), перезапуск не теряет шаг.
- Фоновые задачи под супервизором с экспоненциальной задержкой перезапуска.
- Оповещения владельцам о падениях с антидублированием (`services/alerts.py`).
- Автобэкапы базы (только SQLite) + финальный бэкап при штатной остановке.
- Антиспам в личных сообщениях, безопасная отправка с обработкой ошибок Telegram.
- Корректное завершение по SIGINT/SIGTERM (для systemd).

## Стек

Python 3.11+ · pyTelegramBotAPI 4.21 · SQLAlchemy 2 (async) · Alembic · aiohttp · aiosqlite · pydantic-settings

## Структура

```
main.py              запуск бота (polling + фоновые задачи)
web_server.py        aiohttp-сервер Mini App (API + статика)
config.py            настройки из .env (pydantic-settings)
api/                 REST API Mini App и проверка подписи Telegram
handlers/            support, admin, chat_admin, broadcast, miniapp
services/            бизнес-логика: питомец, игры, магазин, рассылки, бэкапы
database/            модели и подключение к БД
middlewares/         антиспам
filters/             фильтры ролей
alembic/             миграции
miniapp/             фронтенд Mini App (index.html, app.js, styles.css)
```

## Установка

```bash
git clone https://github.com/bifypcoc2-arch/BotPodderzki2.git
cd BotPodderzki2
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # заполнить значения
```

Инициализация базы и первый владелец:

```bash
python init_db.py             # таблицы + начальные данные (товары, слова)
python add_admin.py           # выдать права владельца
```

Запуск (нужны оба процесса):

```bash
python main.py                # бот
python web_server.py          # API и статика Mini App
```

Или одной командой: `./start.sh` (Linux/macOS), `start.bat` (Windows).

## Docker

```bash
cp .env.example .env
docker compose up -d
```

Поднимаются два контейнера: `bot` и `web` (порт 8080).

## Настройка

Обязательные переменные в `.env`:

| Переменная | Описание |
| --- | --- |
| `BOT_TOKEN` | токен бота от @BotFather |
| `DATABASE_URL` | например `sqlite+aiosqlite:///./bot.db` |
| `FORUM_GROUP_ID` | ID группы с включёнными темами (форумом) |
| `MINI_APP_URL` | публичный HTTPS-адрес Mini App |

Остальное имеет значения по умолчанию: логи, бэкапы, антиспам, оповещения, тайминги рассылок и очереди — полный список в `.env.example` и `config.py`.

### Подготовка группы
1. Создать группу, включить **Topics** (темы).
2. Добавить бота администратором с правом управления темами.
3. Указать ID группы в `FORUM_GROUP_ID` (со знаком минус).

### Mini App
1. Отдать `MINI_APP_URL` через HTTPS (reverse proxy на `web_server.py`).
2. Указать этот URL в настройках бота у @BotFather.

## Команды

Пользовательские команды и правила игровой части описаны в [USER_GUIDE.md](USER_GUIDE.md).

Админские команды:

| Команда | Уровень | Действие |
| --- | --- | --- |
| `/close` | админ | закрыть обращение |
| `/spec`, `/unspec` | специалист | назначить/снять ответственного по теме |
| `/ban`, `/unban`, `/bans` | владелец | управление банами |
| `/stats` | владелец | статистика сообщений |
| `/ads` | владелец | рассылки |

## API Mini App

Все запросы проходят через `telegram_auth_middleware`: `user_id` берётся только из проверенной подписи initData, значения из тела запроса игнорируются.

```
GET  /api/pet                    состояние питомца
POST /api/action                 feed | play | wash | sleep | train
GET  /api/stats                  статистика игрока
GET  /api/inventory              инвентарь
GET  /api/shop                   товары
POST /api/shop/buy|use|equip     покупка, использование, экипировка
POST /api/game/dice              кости
POST /api/game/number-whisper    угадай число
GET  /api/game/wordle            состояние Wordle
POST /api/game/wordle/start      новая игра
POST /api/game/wordle/guess      попытка
GET  /api/achievements           достижения
GET  /api/achievements/check     проверить и выдать новые
```

## Деплой (systemd)

В репозитории есть готовые юниты: `telegram-bot.service` и `miniapp-web.service`.

```bash
sudo cp telegram-bot.service miniapp-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot miniapp-web
```

Перед этим поправьте в юнитах пути `WorkingDirectory` и `ExecStart`.

## Обслуживание

- `python check_db.py` — проверка состояния базы.
- `alembic upgrade head` — применение миграций.
- Логи: каталог `logs` (ротация по `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`).
- Бэкапы: каталог `backups`, интервал `BACKUP_INTERVAL_HOURS`, хранится `BACKUP_KEEP` копий. Для PostgreSQL используйте `pg_dump` — бот бэкапит только SQLite.
