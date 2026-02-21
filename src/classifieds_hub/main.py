from __future__ import annotations

import asyncio
import logging

from classifieds_hub.core.config import Settings
from classifieds_hub.core.logging import setup_logging
from classifieds_hub.db.session import create_engine, init_db


async def _bootstrap_db(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        await init_db(engine)
    finally:
        await engine.dispose()


def run() -> None:
    settings = Settings()
    setup_logging(settings.LOG_LEVEL)
    logger = logging.getLogger("classifieds_hub")

    asyncio.run(_bootstrap_db(settings.DATABASE_URL))

    logger.info("Classifieds Hub bootstrap started")
    logger.info("Environment: %s", settings.APP_ENV)
    logger.info("Timezone: %s", settings.TZ)
    logger.info("Cities: %s", ", ".join(settings.TARGET_CITIES))
    logger.info("Run hours: %s", ", ".join(settings.RUN_HOURS_LOCAL))
    logger.info("Aykhal source enabled: %s", settings.SOURCE_AYKHAL_ENABLED)


if __name__ == "__main__":
    run()
