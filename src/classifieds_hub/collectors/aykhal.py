from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from classifieds_hub.core.config import Settings
from classifieds_hub.db.repository import (
    ListingMediaRepository,
    ListingRepository,
    ListingUpsertData,
    RunRepository,
    SourceRepository,
)

logger = logging.getLogger(__name__)

AYKHAL_BOARD_URL = "https://aykhal.info/board"
AYKHAL_BASE_URL = "https://aykhal.info"
LISTING_PATH_RE = re.compile(r"^/board/read(\d+)\.html$")
BOARD_CATEGORY_PATH_RE = re.compile(r"^/board/(\d+)$")
PHONE_RE = re.compile(r"(?:\+?7|8)[\s()\-]*\d[\d\s()\-]{8,}")
PHONE_DIGITS_RE = re.compile(r"\D+")


@dataclass(slots=True)
class BoardListingRef:
    external_id: str
    url: str


@dataclass(slots=True)
class ParsedListing:
    external_id: str
    url: str
    title: str
    description: str | None
    price_text: str | None
    price_value: Decimal | None
    currency: str | None
    city: str | None
    category: str | None
    author_name: str | None
    phone: str | None
    published_at: datetime | None
    raw_payload: str | None
    media_urls: list[str]


class AykhalCollector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _request_text(self, client: httpx.AsyncClient, url: str) -> str:
        # Небольшой retry нужен, потому что сайты объявлений периодически
        # отвечают нестабильно (временные сетевые ошибки).
        last_error: Exception | None = None
        for attempt in range(1, self.settings.REQUEST_RETRIES + 2):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.settings.REQUEST_RETRIES + 1:
                    raise
                logger.warning("Retrying request %s (attempt %s)", url, attempt + 1)
        raise RuntimeError(f"Request failed for {url}: {last_error}")

    def parse_board_refs(self, html: str) -> list[BoardListingRef]:
        # Собираем только карточки объявлений формата /board/readXXXXX.html.
        # И сразу сортируем по external_id по убыванию (новые сверху).
        soup = BeautifulSoup(html, "html.parser")
        refs: list[BoardListingRef] = []
        seen: set[str] = set()

        for a_tag in soup.select('a[href^="/board/read"]'):
            href = a_tag.get("href", "").strip()
            match = LISTING_PATH_RE.match(href)
            if not match:
                continue

            external_id = match.group(1)
            if external_id in seen:
                continue
            seen.add(external_id)

            refs.append(BoardListingRef(external_id=external_id, url=f"{AYKHAL_BASE_URL}{href}"))

        refs.sort(key=lambda item: int(item.external_id), reverse=True)
        return refs

    def parse_listing_detail(self, html: str, ref: BoardListingRef) -> ParsedListing:
        # Тут идёт адаптация разметки aykhal.info в нашу унифицированную модель.
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.select_one("h2")
        title = title_tag.get_text(" ", strip=True) if title_tag else ref.url

        col = soup.select_one("div.col-md-8")
        description = None
        if col:
            p_tag = col.find("p")
            if p_tag:
                description = p_tag.get_text(" ", strip=True)

        city = None
        city_tag = soup.select_one("li i.fa-map-marker")
        if city_tag and city_tag.parent:
            city_link = city_tag.parent.find("a")
            if city_link:
                city = self.normalize_city(city_link.get_text(" ", strip=True))

        date_text = None
        date_tag = soup.select_one("li i.fa-calendar")
        if date_tag and date_tag.parent:
            date_text = date_tag.parent.get_text(" ", strip=True)
        published_at = self.parse_date(date_text)

        price_text = None
        price_li = soup.select_one("li i.fa-rub")
        if price_li and price_li.parent:
            price_text = price_li.parent.get_text(" ", strip=True)
        price_value, currency = self.parse_price(price_text)

        author_name = None
        author_tag = soup.select_one("li a[href^='/users/']")
        if author_tag:
            author_name = author_tag.get_text(" ", strip=True)

        category = None
        # Категорию берем по ссылке формата /board/<id> (например /board/102).
        # Это устойчивее, чем "-2 в списке ссылок", где может оказаться город.
        for link in soup.select("a[href]"):
            href = (link.get("href") or "").strip()
            if BOARD_CATEGORY_PATH_RE.match(href):
                category = self.normalize_category(link.get_text(" ", strip=True))
                break

        phone = None
        phone_matches = PHONE_RE.findall(soup.get_text(" ", strip=True))
        if phone_matches:
            phone = self.normalize_phone(phone_matches[0])

        media_urls = self.extract_media_urls(soup)

        return ParsedListing(
            external_id=ref.external_id,
            url=ref.url,
            title=title,
            description=description,
            price_text=price_text,
            price_value=price_value,
            currency=currency,
            city=city,
            category=category,
            author_name=author_name,
            phone=phone,
            published_at=published_at,
            # Сохраняем HTML карточки как слепок источника в БД.
            raw_payload=html,
            media_urls=media_urls,
        )

    def _normalize_media_url(self, value: str) -> str:
        normalized = value
        if normalized.startswith("//"):
            normalized = f"https:{normalized}"
        elif normalized.startswith("/"):
            normalized = f"{AYKHAL_BASE_URL}{normalized}"

        # На aykhal.info часто есть миниатюры в /images/board/small/.
        # Для карточек берем оригинал из /images/board/.
        normalized = normalized.replace("/images/board/small/", "/images/board/")
        return normalized

    def extract_media_urls(self, soup: BeautifulSoup) -> list[str]:
        # Пробуем собрать картинки из карточки: сначала прямые img, потом href на изображения.
        candidates: list[str] = []

        for img_tag in soup.select("img[src]"):
            src = (img_tag.get("src") or "").strip()
            if not src:
                continue
            src = self._normalize_media_url(src)
            if src.startswith("http"):
                candidates.append(src)

        for link_tag in soup.select("a[href]"):
            raw_href = (link_tag.get("href") or "").strip()
            if not raw_href:
                continue
            href = raw_href.lower()
            if href.endswith((".jpg", ".jpeg", ".png", ".webp")):
                href = self._normalize_media_url(raw_href)
                candidates.append(href)

        unique: list[str] = []
        seen: set[str] = set()
        for url in candidates:
            lowered = url.lower()
            # Отбрасываем служебные/трекерные иконки и счетчики.
            if any(
                banned in lowered
                for banned in [
                    "yandex.ru",
                    "informer",
                    "mc.yandex",
                    "/templates/",
                    "logo_",
                    "favicon",
                    "no_pic",
                    "nopic",
                ]
            ):
                continue

            # Оставляем только реальные каталоги медиа объявления.
            if not (
                "/images/board/" in lowered
                or "/uploads/" in lowered
                or "/upload/" in lowered
            ):
                continue

            if url in seen:
                continue
            seen.add(url)
            unique.append(url)
        return unique

    def parse_price(self, value: str | None) -> tuple[Decimal | None, str | None]:
        # Разбираем различные форматы цен:
        # - обычные числа
        # - "млн"
        # - "договорная" и пустые значения
        if not value:
            return None, None

        lowered = value.lower()
        if "договор" in lowered or value.strip() in {".", "..."}:
            return None, "RUB"

        cleaned = lowered.replace("рублей", "").replace("руб", "")
        cleaned = cleaned.replace(" ", "").replace(",", ".")

        multiplier = Decimal("1")
        if "млн" in cleaned:
            multiplier = Decimal("1000000")
            cleaned = cleaned.replace("млн", "")

        digits = re.sub(r"[^0-9.]", "", cleaned)
        if not digits:
            return None, "RUB"

        try:
            amount = Decimal(digits) * multiplier
        except Exception:  # noqa: BLE001
            return None, "RUB"
        return amount.quantize(Decimal("0.01")), "RUB"

    def parse_date(self, value: str | None) -> datetime | None:
        # Даты на сайте в формате dd.mm.yyyy.
        # Сразу переводим в timezone-aware UTC.
        if not value:
            return None
        match = re.search(r"(\d{2}\.\d{2}\.\d{4})", value)
        if not match:
            return None
        dt_local = datetime.strptime(match.group(1), "%d.%m.%Y")
        return dt_local.replace(tzinfo=UTC)

    def normalize_city(self, value: str | None) -> str | None:
        # Нормализация нужна, чтобы фильтрация по городам работала стабильно,
        # даже если источник пишет "п. Айхал" или другую вариацию.
        if not value:
            return None

        lowered = value.lower()
        if "айхал" in lowered:
            return "Aykhal"
        if "удач" in lowered:
            return "Udachny"
        return value.strip()

    def normalize_category(self, value: str | None) -> str | None:
        # На уровне источника названия категорий могут быть разные.
        # Здесь приводим к "нашим" базовым категориям для бота.
        if not value:
            return None
        lowered = value.lower().strip()
        mapping = {
            "недвижимость": "Недвижимость",
            "услуги": "Услуги",
            "работа": "Работа",
            "барахолка": "Товары",
            "продажа": "Товары",
            "транспорт": "Транспорт",
        }
        for key, normalized in mapping.items():
            if key in lowered:
                return normalized
        return value.strip()

    def normalize_phone(self, value: str | None) -> str | None:
        # Приводим телефон к единому виду +7XXXXXXXXXX.
        if not value:
            return None

        digits = PHONE_DIGITS_RE.sub("", value)
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]

        if len(digits) == 11 and digits.startswith("7"):
            return f"+{digits}"
        return None

    def build_content_hash(self, parsed: ParsedListing) -> str:
        # Мягкий дедуп: если URL изменится, но смысл объявления тот же,
        # этот хеш поможет это обнаружить на следующих этапах.
        raw = "|".join(
            [
                parsed.title or "",
                parsed.description or "",
                parsed.price_text or "",
                parsed.city or "",
                parsed.phone or "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def collect_once(
        self,
        *,
        source_repo: SourceRepository,
        listing_repo: ListingRepository,
        media_repo: ListingMediaRepository,
        run_repo: RunRepository,
    ) -> dict[str, int]:
        source = await source_repo.get_or_create(
            code="aykhal_info",
            name="Aykhal.info",
            source_type="website",
            enabled=self.settings.SOURCE_AYKHAL_ENABLED,
        )
        run = await run_repo.start(source_id=source.id, run_type="scheduled")

        found_count = 0
        new_count = 0
        updated_count = 0
        backfilled_existing = 0

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.REQUEST_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                board_html = await self._request_text(client, AYKHAL_BOARD_URL)
                refs = self.parse_board_refs(board_html)

                # Ранний стоп: если подряд встретили несколько уже известных карточек,
                # считаем, что до новых мы уже дошли и дальше идти не нужно.
                seen_existing_in_a_row = 0
                for ref in refs:
                    found_count += 1
                    existing = await listing_repo.get_by_source_and_url(source.id, ref.url)
                    if existing:
                        parsed_existing: ParsedListing | None = None
                        need_metadata_refresh = existing.category in {
                            None,
                            "",
                            "Айхал",
                            "Aykhal",
                            "Удачный",
                            "Udachny",
                        }
                        need_payload_refresh = not (existing.raw_payload or "").strip()

                        if (
                            (need_metadata_refresh or need_payload_refresh)
                            and backfilled_existing < self.settings.EXISTING_BACKFILL_PER_RUN
                        ):
                            detail_html = await self._request_text(client, ref.url)
                            parsed = self.parse_listing_detail(detail_html, ref)
                            parsed_existing = parsed
                            await listing_repo.upsert(
                                ListingUpsertData(
                                    source_id=source.id,
                                    external_id=parsed.external_id,
                                    url=parsed.url,
                                    title=parsed.title,
                                    description=parsed.description,
                                    price_value=parsed.price_value,
                                    price_text=parsed.price_text,
                                    currency=parsed.currency,
                                    city=parsed.city,
                                    category=parsed.category,
                                    author_name=parsed.author_name,
                                    phone=parsed.phone,
                                    published_at=parsed.published_at,
                                    content_hash=self.build_content_hash(parsed),
                                    raw_payload=parsed.raw_payload,
                                )
                            )
                            backfilled_existing += 1

                        # Если запись уже есть, но без медиа, сделаем легкий backfill картинок.
                        has_media = await media_repo.has_media(listing_id=existing.id)
                        if not has_media:
                            if parsed_existing is None:
                                detail_html = await self._request_text(client, ref.url)
                                parsed_existing = self.parse_listing_detail(detail_html, ref)
                            await media_repo.replace_for_listing(
                                listing_id=existing.id,
                                urls=parsed_existing.media_urls,
                            )

                        seen_existing_in_a_row += 1
                        if (
                            seen_existing_in_a_row >= 2
                            and backfilled_existing >= self.settings.EXISTING_BACKFILL_PER_RUN
                        ):
                            break
                        continue

                    seen_existing_in_a_row = 0

                    detail_html = await self._request_text(client, ref.url)
                    parsed = self.parse_listing_detail(detail_html, ref)

                    if parsed.city and parsed.city not in self.settings.TARGET_CITIES:
                        continue

                    listing, created = await listing_repo.upsert(
                        ListingUpsertData(
                            source_id=source.id,
                            external_id=parsed.external_id,
                            url=parsed.url,
                            title=parsed.title,
                            description=parsed.description,
                            price_value=parsed.price_value,
                            price_text=parsed.price_text,
                            currency=parsed.currency,
                            city=parsed.city,
                            category=parsed.category,
                            author_name=parsed.author_name,
                            phone=parsed.phone,
                            published_at=parsed.published_at,
                            content_hash=self.build_content_hash(parsed),
                            raw_payload=parsed.raw_payload,
                        )
                    )
                    await media_repo.replace_for_listing(
                        listing_id=listing.id,
                        urls=parsed.media_urls,
                    )
                    if created:
                        new_count += 1
                    else:
                        updated_count += 1

            await run_repo.finish_success(
                run,
                found_count=found_count,
                new_count=new_count,
                updated_count=updated_count,
            )
            await listing_repo.mark_expired()
            return {
                "found_count": found_count,
                "new_count": new_count,
                "updated_count": updated_count,
            }
        except Exception as exc:  # noqa: BLE001
            await run_repo.finish_error(run, error_text=str(exc))
            raise
