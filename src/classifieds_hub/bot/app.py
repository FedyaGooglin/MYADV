from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError

from classifieds_hub.bot.handlers import create_router
from classifieds_hub.core.config import Settings
from classifieds_hub.core.logging import setup_logging
from classifieds_hub.db.session import create_engine, create_session_factory, init_db


async def run_polling(settings: Settings) -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    engine = create_engine(settings.DATABASE_URL)
    session_factory = create_session_factory(settings.DATABASE_URL)
    await init_db(engine)
    logger = logging.getLogger(__name__)

    backoff_seconds = 2

    try:
        while True:
            # Используем стандартную сессию aiogram, чтобы учитывать прокси окружения.
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            dp = Dispatcher()
            dp.include_router(create_router(session_factory, settings))

            try:
                await dp.start_polling(bot)
                break
            except TelegramNetworkError as exc:
                logger.warning(
                    "Telegram network error: %s. Retrying in %ss",
                    exc,
                    backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)
            finally:
                await bot.session.close()
    finally:
        await engine.dispose()


def run_cli() -> None:
    settings = Settings()
    setup_logging(settings.LOG_LEVEL)
    logging.getLogger(__name__).info("Starting Telegram bot polling")
    asyncio.run(run_polling(settings))


if __name__ == "__main__":
    run_cli()
