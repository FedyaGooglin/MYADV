from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from classifieds_hub.bot.delivery import send_subscription_updates
from classifieds_hub.collectors.aykhal import AykhalCollector
from classifieds_hub.core.config import Settings
from classifieds_hub.core.logging import setup_logging
from classifieds_hub.db.repository import (
    ListingMediaRepository,
    ListingRepository,
    RunRepository,
    SourceRepository,
)
from classifieds_hub.db.session import create_engine, create_session_factory, init_db


async def run_once(settings: Settings) -> dict[str, int]:
    # Отдельный one-shot раннер удобен для отладки и ручного запуска по cron.
    engine = create_engine(settings.DATABASE_URL)
    session_factory = create_session_factory(settings.DATABASE_URL)
    await init_db(engine)

    try:
        async with session_factory() as session:
            source_repo = SourceRepository(session)
            listing_repo = ListingRepository(session)
            media_repo = ListingMediaRepository(session)
            run_repo = RunRepository(session)

            collector = AykhalCollector(settings)
            stats = await collector.collect_once(
                source_repo=source_repo,
                listing_repo=listing_repo,
                media_repo=media_repo,
                run_repo=run_repo,
            )
            await session.commit()
            return stats
    except Exception:
        logging.getLogger(__name__).exception("Aykhal collection failed")
        raise
    finally:
        await engine.dispose()


async def deliver_updates_after_collect(settings: Settings) -> int:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    # Пока токен-заглушка или пустой — просто пропускаем доставку.
    if not token or token == "replace_me" or ":" not in token:
        return 0

    engine = create_engine(settings.DATABASE_URL)
    session_factory = create_session_factory(settings.DATABASE_URL)
    await init_db(engine)

    bot = Bot(token=token)
    try:
        async with session_factory() as session:
            sent_count = await send_subscription_updates(
                bot=bot,
                session=session,
                max_items_per_subscription=20,
            )
            await session.commit()
            return sent_count
    finally:
        await bot.session.close()
        await engine.dispose()


def run_cli() -> None:
    settings = Settings()
    setup_logging(settings.LOG_LEVEL)
    stats = asyncio.run(run_once(settings))
    logging.getLogger(__name__).info("Collection done: %s", stats)

    if not settings.RUN_ONCE_SEND_DELIVERY:
        logging.getLogger(__name__).info("Delivery after run_once is disabled")
        return

    sent_count = asyncio.run(deliver_updates_after_collect(settings))
    if sent_count:
        logging.getLogger(__name__).info("Subscription delivery sent: %s", sent_count)


if __name__ == "__main__":
    run_cli()
