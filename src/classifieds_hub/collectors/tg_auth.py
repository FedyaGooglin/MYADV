from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError

from classifieds_hub.core.config import Settings
from classifieds_hub.core.logging import setup_logging

logger = logging.getLogger(__name__)


async def authorize_interactive(settings: Settings) -> None:
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        raise RuntimeError("TG_API_ID / TG_API_HASH are required")

    session_name = settings.TG_SESSION_NAME
    client = TelegramClient(session_name, settings.TG_API_ID, settings.TG_API_HASH)

    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info("Session already authorized as: %s", getattr(me, "username", None) or me.id)
            return

        phone = input("Phone (+7XXXXXXXXXX): ").strip()
        sent = await client.send_code_request(phone)
        logger.info("Code delivery type: %s", type(sent.type).__name__)
        logger.info("Code timeout: %s", getattr(sent, "timeout", None))
        logger.info("Next type: %s", type(getattr(sent, "next_type", None)).__name__)

        code = input("Enter code from Telegram app (777000): ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = input("2FA password: ").strip()
            await client.sign_in(password=password)

        me = await client.get_me()
        logger.info("Authorized as: %s", getattr(me, "username", None) or me.id)
    except FloodWaitError as exc:
        raise RuntimeError(f"Telegram flood wait: {exc.seconds}s") from exc
    finally:
        await client.disconnect()


async def authorize_via_qr(settings: Settings) -> None:
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        raise RuntimeError("TG_API_ID / TG_API_HASH are required")

    client = TelegramClient(settings.TG_SESSION_NAME, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info("Session already authorized as: %s", getattr(me, "username", None) or me.id)
            return

        qr = await client.qr_login()
        logger.info("Open this URL on phone Telegram to confirm login:")
        logger.info(qr.url)
        logger.info("Waiting for confirmation up to 180 seconds...")

        await asyncio.wait_for(qr.wait(), timeout=180)
        me = await client.get_me()
        logger.info("Authorized as: %s", getattr(me, "username", None) or me.id)
    finally:
        await client.disconnect()


def reset_session_file(settings: Settings) -> None:
    path = Path(f"{settings.TG_SESSION_NAME}.session")
    if path.exists():
        path.unlink()
        logger.info("Deleted session file: %s", path)
    else:
        logger.info("Session file does not exist: %s", path)


def run_cli() -> None:
    settings = Settings()
    setup_logging(settings.LOG_LEVEL)

    action = input("Action [auth/qr/reset]: ").strip().lower() or "auth"
    if action == "reset":
        reset_session_file(settings)
        return

    if action == "qr":
        asyncio.run(authorize_via_qr(settings))
        return

    asyncio.run(authorize_interactive(settings))


if __name__ == "__main__":
    run_cli()
