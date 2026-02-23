from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps
from telethon import TelegramClient

CARD_SIZE = (1080, 1080)


def _black_square_jpeg() -> bytes:
    image = Image.new("RGB", CARD_SIZE, "black")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _to_square_jpeg(raw: bytes) -> bytes | None:
    try:
        with Image.open(BytesIO(raw)) as img:
            converted = img.convert("RGB")
            fitted = ImageOps.fit(converted, CARD_SIZE, method=Image.Resampling.LANCZOS)
            out = BytesIO()
            fitted.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _parse_tg_media_ref(media_ref: str) -> tuple[str, int] | None:
    # Формат: tgmsg://<chat_ref>/<message_id>
    if not media_ref.startswith("tgmsg://"):
        return None
    payload = media_ref.removeprefix("tgmsg://")
    if "/" not in payload:
        return None
    chat_ref, message_id_raw = payload.rsplit("/", 1)
    if not chat_ref or not message_id_raw.isdigit():
        return None
    return chat_ref, int(message_id_raw)


async def _load_tg_media_bytes(
    *,
    tg_client: TelegramClient | None,
    media_ref: str,
) -> bytes | None:
    if tg_client is None:
        return None

    parsed = _parse_tg_media_ref(media_ref)
    if parsed is None:
        return None
    chat_ref, message_id = parsed

    entity = await tg_client.get_entity(chat_ref)
    message = await tg_client.get_messages(entity, ids=message_id)
    if not message or message.media is None:
        return None

    downloaded = await tg_client.download_media(message, file=bytes)
    if isinstance(downloaded, bytes):
        return downloaded
    return None


async def build_listing_card_photo(
    *,
    client: httpx.AsyncClient,
    media_ref: str | None,
    filename: str,
    tg_client: TelegramClient | None = None,
) -> BufferedInputFile:
    if media_ref:
        try:
            content: bytes | None = None
            if media_ref.startswith("tgmsg://"):
                content = await _load_tg_media_bytes(tg_client=tg_client, media_ref=media_ref)
            elif media_ref.startswith("http://") or media_ref.startswith("https://"):
                response = await client.get(media_ref)
                response.raise_for_status()
                content = response.content
            else:
                local_path = Path(media_ref)
                if local_path.exists() and local_path.is_file():
                    content = local_path.read_bytes()

            normalized = _to_square_jpeg(content) if content else None
            if normalized is not None:
                return BufferedInputFile(normalized, filename=filename)
        except Exception:  # noqa: BLE001
            pass

    return BufferedInputFile(_black_square_jpeg(), filename=filename)
