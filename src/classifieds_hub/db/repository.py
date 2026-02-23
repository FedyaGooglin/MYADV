from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classifieds_hub.db.models import (
    DeliveryLog,
    Listing,
    ListingMedia,
    Run,
    SourceCursor,
    Source,
    Subscription,
    TgRawMessage,
)


def utc_now() -> datetime:
    # Единая функция времени, чтобы проще контролировать UTC-инвариант.
    return datetime.now(timezone.utc)


DEDUPE_TEXT_RE = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)
DEDUPE_DIGITS_RE = re.compile(r"\D+")


@dataclass(slots=True)
class ListingUpsertData:
    source_id: int
    url: str
    title: str
    description: str | None = None
    external_id: str | None = None
    price_value: Decimal | None = None
    price_text: str | None = None
    currency: str | None = None
    city: str | None = None
    district: str | None = None
    category: str | None = None
    author_name: str | None = None
    phone: str | None = None
    published_at: datetime | None = None
    content_hash: str | None = None
    raw_payload: str | None = None


def calc_expires_at(published_at: datetime | None, fetched_at: datetime) -> datetime:
    # Бизнес-правило: объявление "живёт" 30 дней.
    # Если publish date недоступна, считаем от момента обнаружения.
    if published_at is not None:
        return published_at + timedelta(days=30)
    return fetched_at + timedelta(days=30)


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> Source | None:
        stmt: Select[tuple[Source]] = select(Source).where(Source.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self, *, code: str, name: str, source_type: str, enabled: bool = True
    ) -> Source:
        # Важно не плодить дубли источников при каждом запуске.
        existing = await self.get_by_code(code)
        if existing:
            return existing

        source = Source(code=code, name=name, source_type=source_type, enabled=enabled)
        self.session.add(source)
        await self.session.flush()
        return source


class ListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _normalize_for_dedupe(value: str | None) -> str:
        if not value:
            return ""
        lowered = value.lower().replace("ё", "е")
        compact = DEDUPE_TEXT_RE.sub(" ", lowered)
        return " ".join(compact.split())

    @staticmethod
    def _dedupe_key(item: Listing) -> str | None:
        title_key = ListingRepository._normalize_for_dedupe(item.title)
        if len(title_key) < 8:
            return None

        phone_digits = DEDUPE_DIGITS_RE.sub("", item.phone or "")
        if phone_digits:
            return f"p:{phone_digits}|t:{title_key}"

        price_key = ListingRepository._normalize_for_dedupe(item.price_text)
        if price_key:
            return f"t:{title_key}|pr:{price_key}"

        return None

    @staticmethod
    def _title_token_set(title: str) -> set[str]:
        normalized = ListingRepository._normalize_for_dedupe(title)
        tokens = {
            token
            for token in normalized.split()
            if len(token) >= 3 and token not in {"куплю", "продам", "продаю", "срочно"}
        }
        return tokens

    @staticmethod
    def _dedupe_listings(items: list[Listing]) -> list[Listing]:
        unique: list[Listing] = []
        seen: set[str] = set()
        seen_by_phone: dict[str, list[set[str]]] = {}
        for item in items:
            key = ListingRepository._dedupe_key(item)
            if key and key in seen:
                continue

            phone_digits = DEDUPE_DIGITS_RE.sub("", item.phone or "")
            if phone_digits:
                current_tokens = ListingRepository._title_token_set(item.title)
                if current_tokens:
                    phone_history = seen_by_phone.get(phone_digits, [])
                    is_similar_duplicate = False
                    for prev_tokens in phone_history:
                        overlap = len(current_tokens & prev_tokens)
                        union = len(current_tokens | prev_tokens)
                        if union > 0 and (overlap / union) >= 0.6:
                            is_similar_duplicate = True
                            break
                    if is_similar_duplicate:
                        continue

            if key:
                seen.add(key)
            if phone_digits:
                seen_by_phone.setdefault(phone_digits, []).append(
                    ListingRepository._title_token_set(item.title)
                )
            unique.append(item)
        return unique

    async def get_by_source_and_url(self, source_id: int, url: str) -> Listing | None:
        stmt: Select[tuple[Listing]] = select(Listing).where(
            Listing.source_id == source_id, Listing.url == url
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, listing_id: int) -> Listing | None:
        stmt: Select[tuple[Listing]] = select(Listing).where(Listing.id == listing_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_by_external_id(self, *, source_id: int, external_id: str) -> bool:
        stmt: Select[tuple[Listing]] = select(Listing).where(
            Listing.source_id == source_id,
            Listing.external_id == external_id,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def upsert(self, data: ListingUpsertData) -> tuple[Listing, bool]:
        # Основной путь записи: либо новое объявление, либо обновление существующего.
        existing = await self.get_by_source_and_url(data.source_id, data.url)

        if existing is None:
            fetched_at = utc_now()
            expires_at = calc_expires_at(data.published_at, fetched_at)
            listing = Listing(
                source_id=data.source_id,
                external_id=data.external_id,
                url=data.url,
                title=data.title,
                description=data.description,
                price_value=data.price_value,
                price_text=data.price_text,
                currency=data.currency,
                city=data.city,
                district=data.district,
                category=data.category,
                author_name=data.author_name,
                phone=data.phone,
                published_at=data.published_at,
                fetched_at=fetched_at,
                expires_at=expires_at,
                is_expired=expires_at <= fetched_at,
                content_hash=data.content_hash,
                raw_payload=data.raw_payload,
            )
            self.session.add(listing)
            await self.session.flush()
            return listing, True

        existing.external_id = data.external_id
        existing.title = data.title
        existing.description = data.description
        existing.price_value = data.price_value
        existing.price_text = data.price_text
        existing.currency = data.currency
        existing.city = data.city
        existing.district = data.district
        existing.category = data.category
        existing.author_name = data.author_name
        existing.phone = data.phone
        existing.published_at = data.published_at
        existing.fetched_at = utc_now()
        existing.expires_at = calc_expires_at(data.published_at, existing.fetched_at)
        existing.is_expired = existing.expires_at <= existing.fetched_at
        existing.content_hash = data.content_hash
        existing.raw_payload = data.raw_payload
        await self.session.flush()
        return existing, False

    async def mark_expired(self, reference_time: datetime | None = None) -> int:
        # Переводим в expired только те записи, которые еще не просрочены,
        # но уже перешли границу expires_at.
        now = reference_time or utc_now()
        stmt = select(Listing).where(Listing.is_expired.is_(False), Listing.expires_at <= now)
        result = await self.session.execute(stmt)
        changed = 0
        for listing in result.scalars().all():
            listing.is_expired = True
            changed += 1
        await self.session.flush()
        return changed

    async def latest(
        self,
        *,
        city: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[Listing]:
        stmt = select(Listing)
        # По умолчанию отдаём только актуальные объявления.
        if not include_expired:
            stmt = stmt.where(Listing.is_expired.is_(False))
        if city:
            stmt = stmt.where(Listing.city == city)
        if category:
            stmt = stmt.where(Listing.category == category)

        stmt = stmt.order_by(Listing.published_at.desc(), Listing.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_recent(
        self,
        *,
        hours: int = 24,
        city: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[Listing]:
        # Удобный хелпер для /new и /digest: только свежие за N часов.
        since = utc_now() - timedelta(hours=hours)
        stmt = select(Listing).where(Listing.published_at.is_not(None), Listing.published_at >= since)

        if not include_expired:
            stmt = stmt.where(Listing.is_expired.is_(False))
        if city:
            stmt = stmt.where(Listing.city == city)
        if category:
            stmt = stmt.where(Listing.category == category)

        stmt = stmt.order_by(Listing.published_at.desc(), Listing.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_categories(self, *, city: str | None = None) -> list[str]:
        # Категории берем из актуальных объявлений, чтобы меню было "живым".
        # Явно исключаем значения, которые категорией не являются.
        invalid_categories = ["", "Айхал", "Aykhal", "Удачный", "Udachny"]
        stmt = (
            select(Listing.category)
            .where(
                Listing.is_expired.is_(False),
                Listing.category.is_not(None),
                Listing.category.not_in(invalid_categories),
            )
            .group_by(Listing.category)
            .order_by(func.count(Listing.id).desc())
        )
        if city:
            stmt = stmt.where(Listing.city == city)

        result = await self.session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]

    async def list_by_category_page(
        self,
        *,
        category: str,
        city: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Listing], int]:
        # Пагинированная выборка для режима "доска по категории".
        base_filters = [Listing.is_expired.is_(False), Listing.category == category]
        if city:
            base_filters.append(Listing.city == city)

        list_stmt = (
            select(Listing)
            .where(and_(*base_filters))
            .order_by(Listing.published_at.desc(), Listing.id.desc())
        )
        rows = await self.session.execute(list_stmt)
        deduped = self._dedupe_listings(list(rows.scalars().all()))

        total = len(deduped)
        offset = max(0, page) * page_size
        return deduped[offset : offset + page_size], total

    async def search(
        self,
        *,
        query: str | None = None,
        city: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[Listing]:
        # Базовый поиск по title/description + фильтры по городу/категории.
        stmt = select(Listing)
        filters = []

        if not include_expired:
            filters.append(Listing.is_expired.is_(False))
        if city:
            filters.append(Listing.city == city)
        if category:
            filters.append(Listing.category == category)
        if query:
            pattern = f"%{query.lower()}%"
            filters.append(
                or_(
                    func.lower(Listing.title).like(pattern),
                    func.lower(func.coalesce(Listing.description, "")).like(pattern),
                )
            )

        if filters:
            stmt = stmt.where(and_(*filters))

        stmt = stmt.order_by(Listing.published_at.desc(), Listing.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_card_photo_file_id(self, *, listing_id: int, file_id: str) -> None:
        listing = await self.get_by_id(listing_id)
        if listing is None:
            return
        listing.card_photo_file_id = file_id
        await self.session.flush()

    async def set_card_photo_file_ids(self, mapping: dict[int, str]) -> int:
        updated = 0
        for listing_id, file_id in mapping.items():
            listing = await self.get_by_id(listing_id)
            if listing is None:
                continue
            if listing.card_photo_file_id == file_id:
                continue
            listing.card_photo_file_id = file_id
            updated += 1
        await self.session.flush()
        return updated


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, *, source_id: int | None, run_type: str = "scheduled") -> Run:
        run = Run(source_id=source_id, run_type=run_type, status="started", started_at=utc_now())
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_success(
        self, run: Run, *, found_count: int, new_count: int, updated_count: int
    ) -> Run:
        run.status = "ok"
        run.finished_at = utc_now()
        run.found_count = found_count
        run.new_count = new_count
        run.updated_count = updated_count
        await self.session.flush()
        return run

    async def finish_error(self, run: Run, *, error_text: str) -> Run:
        run.status = "failed"
        run.finished_at = utc_now()
        run.error_text = error_text
        await self.session.flush()
        return run


@dataclass(slots=True)
class SubscriptionFilters:
    city: str | None = None
    category: str | None = None

    def to_json(self) -> str:
        payload: dict[str, str] = {}
        if self.city:
            payload["city"] = self.city
        if self.category:
            payload["category"] = self.category
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "SubscriptionFilters":
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return cls()
        return cls(city=data.get("city"), category=data.get("category"))


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, telegram_user_id: int, chat_id: int) -> Subscription | None:
        stmt = select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.chat_id == chat_id,
            Subscription.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_active(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        filters: SubscriptionFilters,
    ) -> Subscription:
        stmt = select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.chat_id == chat_id,
        )
        result = await self.session.execute(stmt)
        sub = result.scalar_one_or_none()

        if sub is None:
            sub = Subscription(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                filters_json=filters.to_json(),
                is_active=True,
            )
            self.session.add(sub)
        else:
            sub.filters_json = filters.to_json()
            sub.is_active = True

        await self.session.flush()
        return sub

    async def deactivate(self, *, telegram_user_id: int, chat_id: int) -> bool:
        stmt = select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.chat_id == chat_id,
            Subscription.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        sub = result.scalar_one_or_none()
        if sub is None:
            return False

        sub.is_active = False
        await self.session.flush()
        return True

    async def list_active(self) -> list[Subscription]:
        stmt = select(Subscription).where(Subscription.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_chat(self, *, telegram_user_id: int, chat_id: int) -> list[Subscription]:
        stmt = select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.chat_id == chat_id,
            Subscription.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate_all_for_chat(self, *, telegram_user_id: int, chat_id: int) -> int:
        stmt = select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.chat_id == chat_id,
            Subscription.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        for sub in rows:
            sub.is_active = False
        await self.session.flush()
        return len(rows)


class DeliveryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def was_sent(self, *, subscription_id: int, listing_id: int) -> bool:
        stmt = select(DeliveryLog).where(
            DeliveryLog.subscription_id == subscription_id,
            DeliveryLog.listing_id == listing_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_sent(
        self,
        *,
        subscription_id: int,
        listing_id: int,
        status: str = "sent",
    ) -> DeliveryLog:
        record = DeliveryLog(
            subscription_id=subscription_id,
            listing_id=listing_id,
            status=status,
        )
        self.session.add(record)
        await self.session.flush()
        return record


class ListingMediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_listing(self, *, listing_id: int, urls: list[str]) -> None:
        # Для простоты MVP перезаписываем набор медиа целиком.
        existing_stmt = select(ListingMedia).where(ListingMedia.listing_id == listing_id)
        existing_result = await self.session.execute(existing_stmt)
        existing = existing_result.scalars().all()
        for row in existing:
            await self.session.delete(row)

        unique_urls: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            unique_urls.append(url)

        for url in unique_urls:
            self.session.add(ListingMedia(listing_id=listing_id, url=url, media_type="image"))

        await self.session.flush()

    async def primary_media_map(self, listing_ids: list[int]) -> dict[int, str]:
        if not listing_ids:
            return {}

        stmt = (
            select(ListingMedia)
            .where(ListingMedia.listing_id.in_(listing_ids))
            .order_by(ListingMedia.listing_id.asc(), ListingMedia.id.asc())
        )
        result = await self.session.execute(stmt)

        by_listing: dict[int, str] = {}
        for row in result.scalars().all():
            if row.listing_id not in by_listing:
                by_listing[row.listing_id] = row.url.replace(
                    "/images/board/small/", "/images/board/"
                )
        return by_listing

    async def has_media(self, *, listing_id: int) -> bool:
        stmt = (
            select(ListingMedia.id)
            .where(
                ListingMedia.listing_id == listing_id,
                or_(
                    ListingMedia.url.like("%/images/board/%"),
                    ListingMedia.url.like("%/uploads/%"),
                    ListingMedia.url.like("%/upload/%"),
                ),
                ListingMedia.url.not_like("%/images/board/small/%"),
                ListingMedia.url.not_like("%no_pic%"),
                ListingMedia.url.not_like("%nopic%"),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def has_any_media(self, *, listing_id: int) -> bool:
        stmt = select(ListingMedia.id).where(ListingMedia.listing_id == listing_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


@dataclass(slots=True)
class TgRawUpsertData:
    source_id: int
    chat_ref: str
    message_id: int
    posted_at: datetime | None
    author_name: str | None
    text: str | None
    has_media: bool
    phone: str | None
    price_text: str | None
    city: str | None
    category: str | None
    is_candidate: bool
    message_link: str | None
    raw_payload: str | None


class TgRawRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, data: TgRawUpsertData) -> tuple[TgRawMessage, bool]:
        stmt = select(TgRawMessage).where(
            TgRawMessage.source_id == data.source_id,
            TgRawMessage.chat_ref == data.chat_ref,
            TgRawMessage.message_id == data.message_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            row = TgRawMessage(
                source_id=data.source_id,
                chat_ref=data.chat_ref,
                message_id=data.message_id,
                posted_at=data.posted_at,
                author_name=data.author_name,
                text=data.text,
                has_media=data.has_media,
                phone=data.phone,
                price_text=data.price_text,
                city=data.city,
                category=data.category,
                is_candidate=data.is_candidate,
                message_link=data.message_link,
                raw_payload=data.raw_payload,
            )
            self.session.add(row)
            await self.session.flush()
            return row, True

        existing.posted_at = data.posted_at
        existing.author_name = data.author_name
        existing.text = data.text
        existing.has_media = data.has_media
        existing.phone = data.phone
        existing.price_text = data.price_text
        existing.city = data.city
        existing.category = data.category
        existing.is_candidate = data.is_candidate
        existing.message_link = data.message_link
        existing.raw_payload = data.raw_payload
        await self.session.flush()
        return existing, False


class SourceCursorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_last_message_id(self, *, source_id: int, cursor_key: str) -> int | None:
        stmt = select(SourceCursor).where(
            SourceCursor.source_id == source_id,
            SourceCursor.cursor_key == cursor_key,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        try:
            return int(row.cursor_value)
        except ValueError:
            return None

    async def set_last_message_id(self, *, source_id: int, cursor_key: str, value: int) -> None:
        stmt = select(SourceCursor).where(
            SourceCursor.source_id == source_id,
            SourceCursor.cursor_key == cursor_key,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            row = SourceCursor(source_id=source_id, cursor_key=cursor_key, cursor_value=str(value))
            self.session.add(row)
        else:
            row.cursor_value = str(value)
            row.updated_at = utc_now()
        await self.session.flush()

    async def delete(self, *, source_id: int, cursor_key: str) -> bool:
        stmt = select(SourceCursor).where(
            SourceCursor.source_id == source_id,
            SourceCursor.cursor_key == cursor_key,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True
