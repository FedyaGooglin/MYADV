from __future__ import annotations

from datetime import UTC

from classifieds_hub.db.models import Listing

TITLE_LIMIT = 80
DESCRIPTION_LIMIT = 120


def _shorten(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    raw = " ".join(text.split())
    if len(raw) <= limit:
        return raw
    if limit <= 3:
        return "." * limit
    return raw[: limit - 3].rstrip() + "..."


def format_listing_extended(item: Listing) -> str:
    # Базовый формат карточки для Telegram:
    # дата, заголовок, описание, цена и ссылка.
    date_text = "без даты"
    if item.published_at is not None:
        dt = item.published_at.astimezone(UTC)
        date_text = dt.strftime("%d.%m.%Y")

    price = item.price_text or "не указана"
    city = item.city or "не указан"
    category = item.category or "не указана"
    desc = _shorten(item.description, limit=220)
    desc_line = f"\nОписание: {desc}" if desc else ""

    return (
        f"Дата: {date_text}\n"
        f"Город: {city}\n"
        f"Категория: {category}\n"
        f"Заголовок: {item.title}\n"
        f"Цена: {price}"
        f"{desc_line}\n"
        f"Ссылка: {item.url}"
    )


def format_listing_card_text(item: Listing) -> str:
    # Backward-compatible alias for card text formatting.
    return format_post_for_telegram(item)


def format_post_for_telegram(
    post: Listing,
    *,
    title_limit: int = TITLE_LIMIT,
    description_limit: int = DESCRIPTION_LIMIT,
) -> str:
    # Единый шаблон caption:
    # 1 строка заголовок + 1 строка короткое описание.
    title = _shorten(post.title, limit=title_limit) or "Без заголовка"
    description = _shorten(post.description, limit=description_limit) or "Без описания"
    return f"{title}\n{description}"


def format_listing_full(item: Listing) -> str:
    # Полное описание карточки по нажатию кнопки в боте.
    date_text = "без даты"
    if item.published_at is not None:
        dt = item.published_at.astimezone(UTC)
        date_text = dt.strftime("%d.%m.%Y")

    price = item.price_text or "не указана"
    city = item.city or "не указан"
    category = item.category or "не указана"
    phone = item.phone or "не указан"
    desc = _shorten(item.description, limit=1500)

    return (
        f"Дата: {date_text}\n"
        f"Город: {city}\n"
        f"Категория: {category}\n"
        f"Заголовок: {item.title}\n"
        f"Цена: {price}\n"
        f"Телефон: {phone}\n"
        f"Описание: {desc or '-'}\n"
        f"Источник: {item.url}"
    )
