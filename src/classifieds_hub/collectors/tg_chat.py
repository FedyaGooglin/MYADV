from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

from classifieds_hub.core.config import Settings
from classifieds_hub.core.logging import setup_logging
from classifieds_hub.db.repository import (
    ListingMediaRepository,
    ListingRepository,
    ListingUpsertData,
    SourceCursorRepository,
    SourceRepository,
    TgRawRepository,
    TgRawUpsertData,
)
from classifieds_hub.db.session import create_engine, create_session_factory, init_db

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?:\+?7|8)[\s()\-]*\d[\d\s()\-]{8,}")
PHONE_DIGITS_RE = re.compile(r"\D+")
PRICE_EXPLICIT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[\s\u00A0]\d{3})+|\d{3,8})(?:[.,]\d+)?\s*"
    r"(?:₽|руб(?:\.|лей)?|р\b|т\.?р\b|тыс\b|к\b)",
    re.IGNORECASE,
)
PRICE_LABEL_RE = re.compile(r"цен[аы]?\s*[:=-]?\s*(\d{2,8})", re.IGNORECASE)
ANY_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,8})(?!\d)")

LOST_FOUND_RE = re.compile(
    r"(нашл[аи]?|найден[оы]?|потерял|потеря(л|ны|на)|утерян|ключ(и|ей)?)",
    re.IGNORECASE,
)
CARPOOL_RE = re.compile(
    r"(попут|попутч|возьм[еу]\s+пассажир|набер[еу]м?\s+пассажир|"
    r"\bеду\b.*(?:удач|айхал|мирн)|\bпоеду\b.*(?:удач|айхал|мирн)|"
    r"(?:удач|айхал|мирн)\s*[-–—>]\s*(?:удач|айхал|мирн).*(?:пассажир|выезд|еду|поеду)|"
    r"пассажир.*(?:удач|айхал|мирн)|уед[уе]т?\s+пассажир)",
    re.IGNORECASE,
)
CARPOOL_MACHINE_RE = re.compile(
    r"(ед[еиё]т\s+машин(?:а|у|е|ы|ой|ам|ами|ах)\b|"
    r"нужн[аоы]?\s+машин(?:а|у|е|ы|ой|ам|ами|ах)\b|"
    r"ищ[еу]м?\s+машин(?:а|у|е|ы|ой|ам|ами|ах)\b|"
    r"машин(?:а|у|е|ы|ой|ам|ами|ах)\s+на\s+(?:айхал|удач|мирн)|"
    r"к\s+новосибирск\w*\s+рейс\w*.*машин(?:а|у|е|ы|ой|ам|ами|ах))",
    re.IGNORECASE,
)
SERVICE_RE = re.compile(
    r"(такси|аэропорт|трансфер|доставк|услуг|ремонт|маникюр|стриж|сантех)",
    re.IGNORECASE,
)
REALTY_RE = re.compile(
    r"(квартир|комнат|секци|студи|посуточ|аренд|гараж|ипотек|жиль|сдам|сниму|"
    r"\bбалок\b|\bбалка\b)",
    re.IGNORECASE,
)
AUTO_RE = re.compile(
    r"(\bавто\b|автомоб|\bваз\b|жигул|тойот|нисан|хонд|киа|хендай|"
    r"мерсед|бмв|\bbmw\b|\bmercedes\b|\bpriora\b|\bgranta\b|\blada\b|"
    r"\bптс\b|\bмото\b|\bvin\b|\bвин\b|\bпробег\b|"
    r"\bзапчаст\w*|\bавтозапчаст\w*)",
    re.IGNORECASE,
)
AUTO_SALE_RE = re.compile(
    r"(продам|продаю|продажа|прода[её]т(?:ся)?|куплю|купим|обменяю|обмен)",
    re.IGNORECASE,
)
AUTO_SERVICE_RE = re.compile(
    r"(автосигнал|сигнализац|шиномонтаж|развал|схожд|автоэлектрик|"
    r"подогревател|ремонт\s+авто|ремонт\s+машин)",
    re.IGNORECASE,
)
APPLIANCE_RE = re.compile(
    r"(посудомоечн\w*\s+машин\w*|стиральн\w*\s+машин\w*|"
    r"швейн\w*\s+машин\w*|кофемашин\w*)",
    re.IGNORECASE,
)
WORK_RE = re.compile(
    r"(ваканс|требуетс?я|ищем\s+сотруд|подработ|работа\b|зарплат|график)",
    re.IGNORECASE,
)
GOODS_RE = re.compile(
    r"(продам|продаю|продажа|прода[её]т(?:ся)?|куплю|купим|отдам|обменяю|в\s*лс|"
    r"писать\s*в\s*лс|в\s*личк)",
    re.IGNORECASE,
)
AD_INTENT_RE = re.compile(
    r"(продам|прода[еёю]т|продажа|отдам|сдам|сда[еёю]т|в\s*аренду|"
    r"аренд[ауы]?|куплю|обменяю|в\s*лс|в\s*личк|обращат|звонит)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ClassifiedText:
    city: str | None
    category: str | None
    phone: str | None
    price_text: str | None
    is_candidate: bool


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = PHONE_DIGITS_RE.sub("", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def detect_city(text: str) -> str | None:
    lowered = text.lower()
    if "айхал" in lowered:
        return "Aykhal"
    if "удач" in lowered:
        return "Udachny"
    return None


def detect_category(text: str) -> str | None:
    # Приоритет важен: сначала точные и специальные категории,
    # затем более общие.
    if LOST_FOUND_RE.search(text):
        return "Потери, находки"
    if CARPOOL_RE.search(text) or CARPOOL_MACHINE_RE.search(text):
        return "Ищу попутчика"
    if APPLIANCE_RE.search(text):
        return "Товары"
    if AUTO_SERVICE_RE.search(text):
        return "Услуги"
    if SERVICE_RE.search(text):
        return "Услуги"
    if REALTY_RE.search(text):
        return "Недвижимость"
    if WORK_RE.search(text):
        return "Работа"
    if AUTO_RE.search(text) and AUTO_SALE_RE.search(text):
        return "Авто"
    if GOODS_RE.search(text):
        return "Товары"
    return None


def extract_price_text(text: str) -> str | None:
    m = PRICE_EXPLICIT_RE.search(text)
    if m:
        return " ".join(m.group(1).split())

    m = PRICE_LABEL_RE.search(text)
    if m:
        return " ".join(m.group(1).split())

    if AD_INTENT_RE.search(text):
        m = ANY_NUMBER_RE.search(text)
        if m:
            return m.group(1)
    return None


def classify_message_text(text: str, *, has_media: bool, strict: bool) -> ClassifiedText:
    phone_match = PHONE_RE.search(text)
    phone = normalize_phone(phone_match.group(0)) if phone_match else None
    price_text = extract_price_text(text)
    city = detect_city(text)
    category = detect_category(text)
    ad_intent = AD_INTENT_RE.search(text) is not None

    # Если явно есть цена, но категория не распознана,
    # считаем это товарным объявлением по умолчанию.
    if category is None and price_text is not None:
        category = "Товары"

    core_evidence = bool(phone or price_text or ad_intent)

    score = 0
    if phone:
        score += 2
    if price_text:
        score += 1
    if category:
        score += 1
    if ad_intent:
        score += 2
    if has_media:
        score += 1
    if len(text) >= 40:
        score += 1

    # Для общих категорий без явных маркеров объявления ослабляем оценку.
    if strict and category in {"Товары", "Недвижимость", "Услуги", "Работа", "Авто"}:
        if not core_evidence:
            score -= 1

    threshold = 3 if strict else 2
    return ClassifiedText(
        city=city,
        category=category,
        phone=phone,
        price_text=price_text,
        is_candidate=score >= threshold,
    )


def build_message_link(chat_username: str | None, message_id: int) -> str | None:
    if not chat_username:
        return None
    return f"https://t.me/{chat_username}/{message_id}"


def pick_title(text: str, message_id: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"Telegram post #{message_id}"

    city_only_re = re.compile(r"^(?:г\.?\s*)?(удачн\w*|айхал)\.?$", re.IGNORECASE)
    city_prefix_re = re.compile(
        r"^(?:описани[ея]\s*[:\-]\s*)?(?:г\.?\s*)?(?:удачн\w*|айхал)\b[\s,.:;\-]*",
        re.IGNORECASE,
    )
    description_prefix_re = re.compile(r"^описани[ея]\s*[:\-]\s*", re.IGNORECASE)

    for raw in lines:
        cleaned = description_prefix_re.sub("", raw).strip()
        cleaned = city_prefix_re.sub("", cleaned).strip(" ,.;:-")
        if cleaned and not city_only_re.match(cleaned) and len(cleaned) >= 8:
            return cleaned[:180]

    first = description_prefix_re.sub("", lines[0]).strip()
    first = city_prefix_re.sub("", first).strip(" ,.;:-")
    if first:
        return first[:180]
    return f"Telegram post #{message_id}"


def pick_author_name(msg: Message) -> str | None:
    sender = getattr(msg, "sender", None)
    if sender is not None:
        username = getattr(sender, "username", None)
        first_name = getattr(sender, "first_name", None)
        last_name = getattr(sender, "last_name", None)
        parts = [p for p in [first_name, last_name] if p]
        full_name = " ".join(parts).strip()
        if full_name:
            return full_name
        if username:
            return f"@{username}"

    sender_id = getattr(msg, "sender_id", None)
    if sender_id is not None:
        return f"tg_user_{sender_id}"
    return None


def build_tg_media_ref(chat_ref: str, message_id: int) -> str:
    # В БД храним ссылку-указатель на Telegram message media,
    # а не локальный файл. Далее бот сможет получить bytes через Telethon
    # и закешировать Bot API file_id.
    return f"tgmsg://{chat_ref}/{message_id}"


def pick_nearby_media_message_id(
    *,
    current_message_id: int,
    current_posted_at: datetime | None,
    current_grouped_id: int | None,
    recent_media: list[tuple[int, datetime | None, int | None]],
) -> int | None:
    # 1) Предпочитаем то же grouped_id (альбом).
    if current_grouped_id is not None:
        for media_message_id, _, media_grouped_id in recent_media:
            if media_grouped_id == current_grouped_id:
                return media_message_id

    # 2) Иначе берем соседние сообщения (до +-2 id и до 5 минут),
    # что покрывает кейс "текст рядом с фото".
    for media_message_id, media_posted_at, _ in recent_media:
        if abs(media_message_id - current_message_id) > 2:
            continue
        if current_posted_at and media_posted_at:
            diff = abs((media_posted_at - current_posted_at).total_seconds())
            if diff > 300:
                continue
        return media_message_id
    return None


class TgChatCollector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def collect_once(self) -> dict[str, int]:
        if not self.settings.TG_API_ID or not self.settings.TG_API_HASH:
            raise RuntimeError("TG_API_ID / TG_API_HASH are required for tg_chat collector")

        engine = create_engine(self.settings.DATABASE_URL)
        session_factory = create_session_factory(self.settings.DATABASE_URL)
        await init_db(engine)

        total_found = 0
        total_raw_new = 0
        total_listing_new = 0

        client = TelegramClient(
            self.settings.TG_SESSION_NAME,
            self.settings.TG_API_ID,
            self.settings.TG_API_HASH,
        )
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run: "
                "python -m classifieds_hub.collectors.tg_auth"
            )

        try:
            async with session_factory() as session:
                source_repo = SourceRepository(session)
                raw_repo = TgRawRepository(session)
                listing_repo = ListingRepository(session)
                media_repo = ListingMediaRepository(session)
                cursor_repo = SourceCursorRepository(session)

                source = await source_repo.get_or_create(
                    code="tg_chat",
                    name="Telegram chat collector",
                    source_type="telegram",
                    enabled=True,
                )

                now = datetime.now(UTC)
                cutoff = now - timedelta(days=self.settings.TG_BACKFILL_DAYS)

                for chat_ref in self.settings.TG_SOURCE_CHATS:
                    entity = await client.get_entity(chat_ref)
                    username = getattr(entity, "username", None)
                    cursor_key = f"chat:{chat_ref}"
                    backfill_key = f"chat_backfill:{chat_ref}"
                    last_message_id = await cursor_repo.get_last_message_id(
                        source_id=source.id,
                        cursor_key=cursor_key,
                    )
                    backfill_max_id = await cursor_repo.get_last_message_id(
                        source_id=source.id,
                        cursor_key=backfill_key,
                    )

                    max_seen_id = last_message_id or 0
                    min_seen_id: int | None = None
                    processed = 0
                    chat_raw_new = 0
                    chat_listing_new = 0
                    reached_cutoff = False
                    recent_media: list[tuple[int, datetime | None, int | None]] = []

                    logger.info(
                        "TG chat start: chat=%s backfill_days=%s last_message_id=%s backfill_max_id=%s limit_per_run=%s delay=%.2fs",
                        chat_ref,
                        self.settings.TG_BACKFILL_DAYS,
                        last_message_id,
                        backfill_max_id,
                        self.settings.TG_MAX_MESSAGES_PER_RUN,
                        self.settings.TG_DELAY_SECONDS,
                    )

                    if last_message_id:
                        iterator = client.iter_messages(
                            entity,
                            min_id=last_message_id,
                            reverse=True,
                            limit=self.settings.TG_MAX_MESSAGES_PER_RUN,
                        )
                    else:
                        kwargs = {
                            "reverse": False,
                            "limit": self.settings.TG_MAX_MESSAGES_PER_RUN,
                        }
                        if backfill_max_id:
                            kwargs["max_id"] = backfill_max_id
                        iterator = client.iter_messages(
                            entity,
                            **kwargs,
                        )

                    async for msg in iterator:
                        if not isinstance(msg, Message):
                            continue
                        if msg.id is None:
                            continue

                        processed += 1
                        total_found += 1

                        posted_at = msg.date.astimezone(UTC) if msg.date else None
                        if posted_at and posted_at < cutoff and not last_message_id:
                            reached_cutoff = True
                            break

                        if min_seen_id is None or msg.id < min_seen_id:
                            min_seen_id = msg.id

                        text = (msg.message or "").strip()
                        has_media = msg.media is not None
                        is_reply = msg.reply_to is not None
                        grouped_id = getattr(msg, "grouped_id", None)
                        if has_media:
                            recent_media.append((msg.id, posted_at, grouped_id))
                            if len(recent_media) > 20:
                                recent_media.pop(0)

                        classified = classify_message_text(
                            text,
                            has_media=has_media,
                            strict=self.settings.TG_STRICT_CLASSIFICATION,
                        )

                        # Для чата uda4niy по умолчанию считаем город Удачный,
                        # если в тексте явно не указан Айхал.
                        if classified.city is None and chat_ref.lower() == "uda4niy":
                            classified.city = "Udachny"

                        # Реплаи не публикуем как отдельные объявления.
                        if is_reply:
                            classified.is_candidate = False

                        # Категория по умолчанию для кандидатов.
                        if classified.is_candidate and classified.category is None:
                            classified.category = "Разное"

                        raw_payload = None
                        try:
                            raw_payload = json.dumps(msg.to_dict(), ensure_ascii=False, default=str)
                        except Exception:  # noqa: BLE001
                            raw_payload = None

                        message_link = build_message_link(username, msg.id)
                        author_name = pick_author_name(msg)
                        raw_row, raw_created = await raw_repo.upsert(
                            TgRawUpsertData(
                                source_id=source.id,
                                chat_ref=chat_ref,
                                message_id=msg.id,
                                posted_at=posted_at,
                                author_name=author_name,
                                text=text,
                                has_media=has_media,
                                phone=classified.phone,
                                price_text=classified.price_text,
                                city=classified.city,
                                category=classified.category,
                                is_candidate=classified.is_candidate,
                                message_link=message_link,
                                raw_payload=raw_payload,
                            )
                        )
                        if raw_created:
                            total_raw_new += 1
                            chat_raw_new += 1

                        if classified.is_candidate and text:
                            listing_url = message_link or f"tg://{chat_ref}/{msg.id}"
                            listing, created = await listing_repo.upsert(
                                ListingUpsertData(
                                    source_id=source.id,
                                    external_id=f"{chat_ref}:{msg.id}",
                                    url=listing_url,
                                    title=pick_title(text, msg.id),
                                    description=text,
                                    price_text=classified.price_text,
                                    currency="RUB" if classified.price_text else None,
                                    city=classified.city,
                                    category=classified.category,
                                    author_name=author_name,
                                    phone=classified.phone,
                                    published_at=posted_at,
                                    raw_payload=raw_row.raw_payload,
                                )
                            )

                            media_message_id: int | None = None
                            if has_media:
                                media_message_id = msg.id
                            else:
                                media_message_id = pick_nearby_media_message_id(
                                    current_message_id=msg.id,
                                    current_posted_at=posted_at,
                                    current_grouped_id=grouped_id,
                                    recent_media=recent_media,
                                )

                            if media_message_id is not None:
                                has_any = await media_repo.has_any_media(listing_id=listing.id)
                                if not has_any:
                                    await media_repo.replace_for_listing(
                                        listing_id=listing.id,
                                        urls=[build_tg_media_ref(chat_ref, media_message_id)],
                                    )

                            if created:
                                total_listing_new += 1
                                chat_listing_new += 1
                        else:
                            await listing_repo.delete_by_external_id(
                                source_id=source.id,
                                external_id=f"{chat_ref}:{msg.id}",
                            )

                        if msg.id > max_seen_id:
                            max_seen_id = msg.id

                        await asyncio.sleep(self.settings.TG_DELAY_SECONDS)
                        if processed % 20 == 0:
                            await session.commit()
                            logger.info(
                                "TG chat progress: chat=%s processed=%s raw_new=%s listing_new=%s max_seen_id=%s",
                                chat_ref,
                                processed,
                                chat_raw_new,
                                chat_listing_new,
                                max_seen_id,
                            )
                        if processed >= self.settings.TG_MAX_MESSAGES_PER_RUN:
                            break

                    if last_message_id:
                        if max_seen_id > last_message_id:
                            await cursor_repo.set_last_message_id(
                                source_id=source.id,
                                cursor_key=cursor_key,
                                value=max_seen_id,
                            )
                    else:
                        if reached_cutoff and max_seen_id > 0:
                            await cursor_repo.set_last_message_id(
                                source_id=source.id,
                                cursor_key=cursor_key,
                                value=max_seen_id,
                            )
                            await cursor_repo.delete(source_id=source.id, cursor_key=backfill_key)
                        elif min_seen_id is not None:
                            await cursor_repo.set_last_message_id(
                                source_id=source.id,
                                cursor_key=backfill_key,
                                value=min_seen_id,
                            )

                    await session.commit()
                    logger.info(
                        "TG chat done: chat=%s processed=%s raw_new=%s listing_new=%s new_cursor=%s reached_cutoff=%s",
                        chat_ref,
                        processed,
                        chat_raw_new,
                        chat_listing_new,
                        max_seen_id,
                        reached_cutoff,
                    )

            return {
                "found_count": total_found,
                "raw_new_count": total_raw_new,
                "listing_new_count": total_listing_new,
            }
        except FloodWaitError as exc:
            logger.warning("Telegram FloodWait %ss", exc.seconds)
            raise
        finally:
            await client.disconnect()
            await engine.dispose()


def run_cli() -> None:
    settings = Settings()
    setup_logging(settings.LOG_LEVEL)
    stats = asyncio.run(TgChatCollector(settings).collect_once())
    logger.info("TG collection done: %s", stats)


if __name__ == "__main__":
    run_cli()
