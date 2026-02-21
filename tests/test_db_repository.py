from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from classifieds_hub.db.repository import ListingRepository, ListingUpsertData, SourceRepository
from classifieds_hub.db.session import create_engine, init_db


def test_source_get_or_create(tmp_path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "test_sources.sqlite3"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)

        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                sources = SourceRepository(session)

                first = await sources.get_or_create(
                    code="aykhal_info",
                    name="Aykhal.info",
                    source_type="website",
                )
                second = await sources.get_or_create(
                    code="aykhal_info",
                    name="Aykhal.info",
                    source_type="website",
                )
                await session.commit()

                assert first.id == second.id
                assert first.code == "aykhal_info"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_listing_upsert_and_latest_filter(tmp_path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "test_listings.sqlite3"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)

        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                sources = SourceRepository(session)
                listings = ListingRepository(session)

                source = await sources.get_or_create(
                    code="aykhal_info",
                    name="Aykhal.info",
                    source_type="website",
                )

                payload = ListingUpsertData(
                    source_id=source.id,
                    external_id="100811",
                    url="https://aykhal.info/board/read100811.html",
                    title="Продам 3-комнатную квартиру",
                    description="Хорошая квартира в центре.",
                    city="Aykhal",
                    category="Недвижимость",
                    price_text="3 000 000",
                    published_at=datetime.now(UTC),
                )

                created_listing, created = await listings.upsert(payload)
                assert created is True

                payload.title = "Продам 3-комнатную квартиру (обновлено)"
                updated_listing, created_second = await listings.upsert(payload)
                assert created_second is False
                assert created_listing.id == updated_listing.id

                only_city = await listings.latest(city="Aykhal", limit=10)
                assert len(only_city) == 1
                assert only_city[0].title.endswith("(обновлено)")

                recent = await listings.latest_recent(hours=24, city="Aykhal", limit=10)
                assert len(recent) == 1

                no_rows = await listings.latest(city="Udachny", limit=10)
                assert no_rows == []

                payload.published_at = datetime.now(UTC) - timedelta(days=40)
                await listings.upsert(payload)
                await listings.mark_expired()

                hidden = await listings.latest(city="Aykhal", limit=10)
                assert hidden == []

                visible_if_requested = await listings.latest(
                    city="Aykhal", limit=10, include_expired=True
                )
                assert len(visible_if_requested) == 1
                assert visible_if_requested[0].is_expired is True

                no_recent_after_expiry = await listings.latest_recent(
                    hours=24, city="Aykhal", limit=10
                )
                assert no_recent_after_expiry == []

                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_list_active_categories_ignores_city_like_values(tmp_path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "test_categories.sqlite3"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)

        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                sources = SourceRepository(session)
                listings = ListingRepository(session)

                source = await sources.get_or_create(
                    code="aykhal_info",
                    name="Aykhal.info",
                    source_type="website",
                )

                await listings.upsert(
                    ListingUpsertData(
                        source_id=source.id,
                        url="https://x/1",
                        title="x",
                        category="Айхал",
                        published_at=datetime.now(UTC),
                    )
                )

                await listings.upsert(
                    ListingUpsertData(
                        source_id=source.id,
                        url="https://x/2",
                        title="y",
                        category="Недвижимость",
                        published_at=datetime.now(UTC),
                    )
                )

                categories = await listings.list_active_categories()
                assert categories == ["Недвижимость"]
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
