from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from classifieds_hub.bot.formatting import format_listing_extended
from classifieds_hub.db.repository import (
    DeliveryLogRepository,
    ListingRepository,
    SubscriptionFilters,
    SubscriptionRepository,
)


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

        text = "\n\n---\n\n".join(format_listing_extended(item) for item in unsent)
        await bot.send_message(sub.chat_id, f"Новые объявления:\n\n{text}")

        for item in unsent:
            await delivery_repo.mark_sent(subscription_id=sub.id, listing_id=item.id)
            total_sent += 1

    return total_sent
