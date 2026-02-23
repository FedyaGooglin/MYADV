from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    # Во всем проекте используем только timezone-aware UTC время.
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Короткий стабильный код источника (например: aykhal_info).
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    listings: Mapped[list[Listing]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        UniqueConstraint("source_id", "url", name="uq_listing_source_url"),
        Index("ix_listings_city_category", "city", "category"),
        Index("ix_listings_published_at", "published_at"),
        Index("ix_listings_price", "price_value"),
        Index("ix_listings_content_hash", "content_hash"),
        Index("ix_listings_is_expired", "is_expired"),
    )

    # Главная сущность объявления в нашей агрегированной базе.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    # external_id берется с сайта-источника (например read100811 -> 100811).
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(String(512))

    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    price_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    district: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # fetched_at — когда мы это объявление в последний раз видели/обновили.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # expires_at = published_at + 30 дней (или от fetched_at, если дата публикации неизвестна).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Флаг просрочки: такие объявления не должны попадать в выдачу бота по умолчанию.
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    # Кэш telegram file_id карточки, чтобы не качать/загружать изображение каждый раз.
    card_photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[Source] = relationship(back_populates="listings")
    media: Mapped[list[ListingMedia]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
    )


class ListingMedia(Base):
    __tablename__ = "listing_media"
    __table_args__ = (
        UniqueConstraint("listing_id", "url", name="uq_listing_media_listing_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(32), default="image")

    listing: Mapped[Listing] = relationship(back_populates="media")


class TgRawMessage(Base):
    __tablename__ = "tg_raw_messages"
    __table_args__ = (
        UniqueConstraint("source_id", "chat_ref", "message_id", name="uq_tg_raw_source_chat_msg"),
        Index("ix_tg_raw_posted_at", "posted_at"),
        Index("ix_tg_raw_is_candidate", "is_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    chat_ref: Mapped[str] = mapped_column(String(255), index=True)
    message_id: Mapped[int] = mapped_column(Integer, index=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)

    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_candidate: Mapped[bool] = mapped_column(Boolean, default=False)

    message_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceCursor(Base):
    __tablename__ = "source_cursors"
    __table_args__ = (
        UniqueConstraint("source_id", "cursor_key", name="uq_source_cursor_source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    cursor_key: Mapped[str] = mapped_column(String(255), index=True)
    cursor_value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_started_at", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    # run_type: scheduled/manual/test и т.д.
    run_type: Mapped[str] = mapped_column(String(32), default="scheduled")
    status: Mapped[str] = mapped_column(String(32), default="started")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    found_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)

    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "chat_id", name="uq_subscription_user_chat"),
        Index("ix_subscriptions_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)

    # Гибкие фильтры подписки храним в JSON-строке.
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DeliveryLog(Base):
    __tablename__ = "delivery_log"
    __table_args__ = (
        UniqueConstraint("subscription_id", "listing_id", name="uq_delivery_sub_listing"),
        Index("ix_delivery_sent_at", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True
    )

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status: Mapped[str] = mapped_column(String(32), default="sent")
