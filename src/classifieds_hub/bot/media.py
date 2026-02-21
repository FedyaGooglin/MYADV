from __future__ import annotations

from io import BytesIO

import httpx
from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps

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


async def build_listing_card_photo(
    *,
    client: httpx.AsyncClient,
    photo_url: str | None,
    filename: str,
) -> BufferedInputFile:
    if photo_url:
        try:
            response = await client.get(photo_url)
            response.raise_for_status()
            normalized = _to_square_jpeg(response.content)
            if normalized is not None:
                return BufferedInputFile(normalized, filename=filename)
        except Exception:  # noqa: BLE001
            pass

    return BufferedInputFile(_black_square_jpeg(), filename=filename)
