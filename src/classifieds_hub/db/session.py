from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from classifieds_hub.db.models import Base


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    # Для SQLite путь может указывать на несуществующую папку.
    # Создаем ее заранее, чтобы приложение не падало на старте.
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        return

    raw_path = database_url.removeprefix(prefix)
    if raw_path == ":memory:" or raw_path.startswith("/"):
        return

    db_path = Path(raw_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)


def create_engine(database_url: str) -> AsyncEngine:
    # В одном месте создаем engine, чтобы поведение было единообразным.
    _ensure_sqlite_parent_dir(database_url)
    return create_async_engine(database_url, future=True)


def create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        # create_all достаточно для MVP и локального SQLite режима.
        await conn.run_sync(Base.metadata.create_all)

        # Легкая "миграция" для SQLite без Alembic на раннем этапе.
        # Если колонки появились в новой версии кода, добавим их динамически.
        rows = await conn.execute(text("PRAGMA table_info(listings)"))
        columns = {row[1] for row in rows}
        if "expires_at" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN expires_at DATETIME"))
        if "is_expired" not in columns:
            await conn.execute(
                text("ALTER TABLE listings ADD COLUMN is_expired BOOLEAN NOT NULL DEFAULT 0")
            )
        if "card_photo_file_id" not in columns:
            await conn.execute(text("ALTER TABLE listings ADD COLUMN card_photo_file_id VARCHAR(255)"))
