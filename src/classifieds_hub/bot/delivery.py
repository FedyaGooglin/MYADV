from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from classifieds_hub.bot.formatting import format_listing_extended
from classifieds_hub.db.models import Listing
from classifieds_hub.db.repository import (
    DeliveryLogRepository,
    ListingRepository,
    SubscriptionFilters,
    SubscriptionRepository,
)

MESSAGE_PREFIX = "Новые объявления:\n\n"
MESSAGE_SEPARATOR = "\n\n---\n\n"
MAX_TELEGRAM_MESSAGE_LEN = 4000
TRUNCATED_SUFFIX = "\n...[сообщение сокращено]"


def _chunk_delivery_items(items: list[Listing]) -> list[tuple[list[Listing], str]]:
    # Telegram ограничивает сообщение 4096 символами,
    # оставим запас и отправим объявления пачками.
    body_limit = MAX_TELEGRAM_MESSAGE_LEN - len(MESSAGE_PREFIX)
    chunks: list[tuple[list[Listing], str]] = []

    current_items: list[Listing] = []
    current_body = ""

    for item in items:
        block = format_listing_extended(item)
        if len(block) > body_limit:
            truncate_to = max(0, body_limit - len(TRUNCATED_SUFFIX))
            block = block[:truncate_to].rstrip() + TRUNCATED_SUFFIX

        candidate_body = block if not current_body else f"{current_body}{MESSAGE_SEPARATOR}{block}"
        if current_items and len(candidate_body) > body_limit:
            chunks.append((current_items, current_body))
            current_items = [item]
            current_body = block
            continue

        current_items.append(item)
        current_body = candidate_body

    if current_items:
        chunks.append((current_items, current_body))

    return chunks


async def send_subscription_updates(
    *,
    bot: Bot,
    session: AsyncSession,
    max_items_per_subscription: int = 20,
) -> int:
    # Рассылка новых объявлений подписчикам.
    # Лимит в 20 нужен, чтобы бот не спамил при всплеске объявлений.
    sub_repo = SubscriptionRepository(session)
    listing_repo = ListingRepository(session)
    delivery_repo = DeliveryLogRepository(session)

    subscriptions = await sub_repo.list_active()
    total_sent = 0

    for sub in subscriptions:
        filters = SubscriptionFilters.from_json(sub.filters_json)
        items = await listing_repo.latest(
            city=filters.city,
            category=filters.category,
            limit=max_items_per_subscription,
        )

        unsent = []
        for item in items:
            already_sent = await delivery_repo.was_sent(
                subscription_id=sub.id,
                listing_id=item.id,
            )
            if not already_sent:
                unsent.append(item)

        if not unsent:
            continue

        for chunk_items, chunk_body in _chunk_delivery_items(unsent):
            await bot.send_message(sub.chat_id, f"{MESSAGE_PREFIX}{chunk_body}")
            for item in chunk_items:
                await delivery_repo.mark_sent(subscription_id=sub.id, listing_id=item.id)
                total_sent += 1

    return total_sent
