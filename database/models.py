from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from database.database import Base


class RoleEnum(enum.Enum):
    ADMIN = "admin"
    SPEC_ADMIN = "spec_admin"
    SENIOR_ADMIN = "senior_admin"
    TECH_ADMIN = "tech_admin"
    CO_OWNER = "co_owner"
    OWNER = "owner"


# Владелец и совладелец. Всё, что доступно только им, проверяется этим уровнем.
OWNER_LEVEL = 5


class TopicStatus(enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    SPEC = "spec"
    CLOSED = "closed"


class BroadcastStatus(enum.Enum):
    DRAFT = "draft"
    SENDING = "sending"
    SENT = "sent"
    DELETED = "deleted"


class PetType(enum.Enum):
    CAT = "cat"
    DOG = "dog"
    FOX = "fox"
    PANDA = "panda"
    RABBIT = "rabbit"
    HEDGEHOG = "hedgehog"
    PENGUIN = "penguin"


class PetStage(enum.Enum):
    CONCEPTION = "conception"
    EGG = "egg"
    BABY = "baby"
    TEEN = "teen"
    ADULT = "adult"


class ItemType(enum.Enum):
    FOOD = "food"
    TOY = "toy"
    ACCESSORY = "accessory"
    BACKGROUND = "background"


class ActionType(enum.Enum):
    SPEC_SET = "spec_set"
    SPEC_UNSET = "spec_unset"
    TOPIC_CLAIMED = "topic_claimed"
    TOPIC_CLOSED = "topic_closed"
    BROADCAST_SENT = "broadcast_sent"
    USER_BANNED = "user_banned"
    USER_UNBANNED = "user_unbanned"
    MEMBER_MUTED = "member_muted"
    MEMBER_UNMUTED = "member_unmuted"
    MEMBER_KICKED = "member_kicked"
    MEMBER_WARNED = "member_warned"
    WARN_REMOVED = "warn_removed"


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pet: Mapped[Optional["UserPet"]] = relationship(back_populates="user", uselist=False)
    inventory: Mapped[list["Inventory"]] = relationship(back_populates="user")
    stats: Mapped[Optional["Stats"]] = relationship(back_populates="user", uselist=False)
    achievements: Mapped[list["UserAchievement"]] = relationship(back_populates="user")
    topics: Mapped[list["Topic"]] = relationship(back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum))
    role_level: Mapped[int] = mapped_column(Integer)
    added_by: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminLog(Base):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(BigInteger)
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType))
    topic_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminStats(Base):
    """Счётчик ответов админов. Отдельная таблица, а не колонка в admins,
    потому что схема поднимается через create_all() без миграций:
    новая таблица создастся сама, новая колонка в существующей — нет."""

    __tablename__ = "admin_stats"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Ban(Base):
    __tablename__ = "bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    banned_by: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    unbanned_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    unbanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Warn(Base):
    """Предупреждение участнику рабочей группы.

    Не путать с Ban: Ban закрывает доступ к поддержке обратившемуся,
    Warn — дисциплинарная отметка сотруднику внутри группы.
    """

    __tablename__ = "warns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    issued_by: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    removed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ChatActivity(Base):
    """Сколько сообщений написал участник в конкретном чате.

    AdminStats считает только ответы, ушедшие пользователю. Здесь —
    весь актив в группе, включая обсуждения между сотрудниками.
    """

    __tablename__ = "chat_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    topic_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus), default=TopicStatus.OPEN)
    claimed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    game_offer_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="topics")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(50))
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[BroadcastStatus] = mapped_column(Enum(BroadcastStatus), default=BroadcastStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)


class UserPet(Base):
    __tablename__ = "user_pets"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    pet_type: Mapped[Optional[PetType]] = mapped_column(Enum(PetType), nullable=True)
    stage: Mapped[PetStage] = mapped_column(Enum(PetStage), default=PetStage.CONCEPTION)
    xp: Mapped[int] = mapped_column(Integer, default=0)

    hunger: Mapped[int] = mapped_column(Integer, default=100)
    happiness: Mapped[int] = mapped_column(Integer, default=100)
    hygiene: Mapped[int] = mapped_column(Integer, default=100)
    energy: Mapped[int] = mapped_column(Integer, default=100)
    discipline: Mapped[int] = mapped_column(Integer, default=100)
    strength: Mapped[int] = mapped_column(Integer, default=0)

    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_feed_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_play_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_wash_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sleep_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_train_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="pet")


class ShopItem(Base):
    __tablename__ = "shop_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    item_type: Mapped[ItemType] = mapped_column(Enum(ItemType))
    price: Mapped[int] = mapped_column(Integer)
    effect_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    effect_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_consumable: Mapped[bool] = mapped_column(Boolean, default=True)


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_items.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    is_equipped: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="inventory")


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    metric_type: Mapped[str] = mapped_column(String(50))
    threshold: Mapped[int] = mapped_column(Integer)


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    achievement_id: Mapped[int] = mapped_column(Integer, ForeignKey("achievements.id"), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="achievements")


class Stats(Base):
    __tablename__ = "stats"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    games_won: Mapped[int] = mapped_column(Integer, default=0)
    win_streak: Mapped[int] = mapped_column(Integer, default=0)
    feedings: Mapped[int] = mapped_column(Integer, default=0)
    baths: Mapped[int] = mapped_column(Integer, default=0)
    sleeps: Mapped[int] = mapped_column(Integer, default=0)
    trainings: Mapped[int] = mapped_column(Integer, default=0)
    login_streak: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="stats")


class GameState(Base):
    """Состояние мини-игр и антифарм-таймеры.

    Слово вордли хранится только здесь, на сервере. Если отдавать его
    в мини-приложение, слово видно в консоли браузера и игра теряет смысл.
    """

    __tablename__ = "game_state"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id"), primary_key=True)

    last_dice_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_number_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    wordle_word: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # JSON-список сделанных попыток, чтобы восстановить поле после закрытия окна
    wordle_attempts: Mapped[str] = mapped_column(Text, default="[]")
    wordle_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    wordle_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    wordle_won: Mapped[bool] = mapped_column(Boolean, default=False)
