from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    forum_group_id: int
    mini_app_url: str

    queue_wait_minutes: int = 5
    broadcast_timeout_minutes: int = 10
    broadcast_delay_ms: int = 50

    # --- База ---
    # echo=True заливает логи текстом каждого SQL-запроса вместе со
    # содержимым обращений. В бою это и шум, и утечка переписки в файлы.
    database_echo: bool = False

    # --- Логи ---
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5

    # --- Бэкапы ---
    # Работают только для SQLite. Для PostgreSQL нужен pg_dump средствами
    # самой СУБД — бот туда не лезет и честно пишет об этом в лог.
    backup_enabled: bool = True
    backup_dir: str = "backups"
    backup_interval_hours: int = 6
    backup_keep: int = 28

    # --- Антиспам в личных сообщениях ---
    antispam_enabled: bool = True
    antispam_window_seconds: int = 15
    antispam_max_messages: int = 6
    antispam_block_seconds: int = 60

    # --- Оповещения владельцам ---
    alerts_enabled: bool = True
    # Один и тот же текст не чаще раза в этот интервал: если сломалось
    # цикличное задание, владелец не получит тысячу одинаковых сообщений.
    alert_repeat_minutes: int = 30
    alert_on_start: bool = False

    # --- Состояние диалогов ---
    # Файл, в котором живут незаконченные диалоги (например, создание
    # рассылки). Без него перезапуск теряет шаг и админ начинает сначала.
    state_file: str = "data/states.pkl"

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')


settings = Settings()
