from __future__ import annotations

import asyncio
import httpx
from aiogram import Router
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classifieds_hub.bot.formatting import format_listing_full, format_post_for_telegram
from classifieds_hub.bot.media import build_listing_card_photo
from classifieds_hub.db.repository import (
    ListingMediaRepository,
    ListingRepository,
    SubscriptionFilters,
    SubscriptionRepository,
)

PAGE_SIZE = 5


class BoardState(StatesGroup):
    choosing_category = State()
    choosing_city = State()
    browsing = State()


def _cities_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Айхал", callback_data="city:Aykhal")],
            [InlineKeyboardButton(text="Удачный", callback_data="city:Udachny")],
            [InlineKeyboardButton(text="Любой город", callback_data="city:any")],
        ]
    )


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    # Главное меню бота: пользователь может работать без команд.
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="Категории")],
            [KeyboardButton(text="Подписки")],
            [KeyboardButton(text="Помощь")],
        ],
    )


def _subscriptions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отключить все подписки", callback_data="sub:off_all")],
            [InlineKeyboardButton(text="В меню", callback_data="flow:menu")],
        ]
    )


async def _send_main_menu(message: Message, text: str) -> None:
    for attempt in range(3):
        try:
            await message.answer(text, reply_markup=_main_menu_keyboard())
            return
        except TelegramNetworkError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.8 * (attempt + 1))


async def _safe_answer_with_keyboard(
    *,
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    # Telegram ограничивает длину текста сообщения.
    # Если карточка страницы слишком длинная, отправим укороченную версию,
    # но не уроним сценарий пользователя.
    payload = text
    for attempt in range(3):
        try:
            await message.answer(payload, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message is too long" in lowered:
                payload = text[:3500].rstrip() + "\n\n[Сообщение сокращено]"
                continue
            raise
        except TelegramNetworkError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.8 * (attempt + 1))


def _categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, category in enumerate(categories):
        rows.append(
            [InlineKeyboardButton(text=category, callback_data=f"cat:{idx}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    max_page = max(0, (total - 1) // PAGE_SIZE)
    prev_page = max(0, page - 1)
    next_page = min(max_page, page + 1)

    nav_row = [
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page:{prev_page}"),
        InlineKeyboardButton(text=f"{page + 1}/{max_page + 1}", callback_data="page:stay"),
        InlineKeyboardButton(text="Дальше ➡️", callback_data=f"page:{next_page}"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            nav_row,
            [InlineKeyboardButton(text="Подписаться на этот фильтр", callback_data="sub:current")],
            [InlineKeyboardButton(text="Сменить категорию", callback_data="flow:categories")],
            [InlineKeyboardButton(text="В меню", callback_data="flow:menu")],
        ]
    )


def _listing_open_keyboard(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть объявление", callback_data=f"detail:{listing_id}"
                )
            ],
        ]
    )


async def _send_listing_card(
    *,
    message: Message,
    text: str,
    listing_id: int,
    cached_file_id: str | None,
    photo_url: str | None,
    media_client: httpx.AsyncClient,
) -> str | None:
    # Отдаем карточку из данных нашей БД.
    # Если фото отсутствует, используем черный квадрат 256x256.
    keyboard = _listing_open_keyboard(listing_id)
    if cached_file_id:
        for attempt in range(3):
            try:
                await message.answer_photo(
                    photo=cached_file_id,
                    caption=text,
                    reply_markup=keyboard,
                )
                return cached_file_id
            except TelegramBadRequest:
                break
            except TelegramNetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.8 * (attempt + 1))

    photo = await build_listing_card_photo(
        client=media_client,
        photo_url=photo_url,
        filename=f"listing_{listing_id}.jpg",
    )
    for attempt in range(3):
        try:
            sent = await message.answer_photo(photo=photo, caption=text, reply_markup=keyboard)
            if sent.photo:
                return sent.photo[-1].file_id
            return None
        except TelegramBadRequest:
            return None
        except TelegramNetworkError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.8 * (attempt + 1))
    return None


def parse_subscribe_args(raw: str | None) -> SubscriptionFilters:
    # Текстовый fallback для подписки оставляем, но основной UX — через кнопки.
    if not raw:
        return SubscriptionFilters()

    city = None
    category = None
    for token in raw.split():
        if token.startswith("city="):
            city = token.removeprefix("city=").strip() or None
        elif token.startswith("category="):
            category = token.removeprefix("category=").strip() or None
    return SubscriptionFilters(city=city, category=category)


async def _send_category_prompt(
    *,
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = ListingRepository(session)
        categories = await repo.list_active_categories()

    if not categories:
        await message.answer("Пока нет актуальных объявлений по категориям.")
        return

    # Храним карту index->category в состоянии, чтобы callback были короткие и надежные.
    await state.clear()
    await state.set_state(BoardState.choosing_category)
    await state.update_data(categories=categories)
    await message.answer("Выбери категорию:", reply_markup=_categories_keyboard(categories))


async def _render_page(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    page: int,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    data = await state.get_data()
    category = data.get("selected_category")
    city = data.get("selected_city")

    if not category:
        await callback.message.answer("Категория не выбрана. Нажми /categories")
        await callback.answer()
        return

    async with session_factory() as session:
        repo = ListingRepository(session)
        media_repo = ListingMediaRepository(session)
        items, total = await repo.list_by_category_page(
            category=category,
            city=city,
            page=page,
            page_size=PAGE_SIZE,
        )
        media_map = await media_repo.primary_media_map([item.id for item in items])

    if total == 0 or not items:
        await callback.message.answer("По выбранному фильтру нет актуальных объявлений.")
        await callback.answer()
        return

    city_title = city or "Любой"

    await state.set_state(BoardState.browsing)
    await state.update_data(current_page=page)
    await callback.message.answer(f"Категория: {category}\nГород: {city_title}")
    new_file_ids: dict[int, str] = {}
    async with httpx.AsyncClient(timeout=20) as media_client:
        for item in items:
            file_id = await _send_listing_card(
                message=callback.message,
                text=format_post_for_telegram(item),
                listing_id=item.id,
                cached_file_id=item.card_photo_file_id,
                photo_url=media_map.get(item.id),
                media_client=media_client,
            )
            if file_id and file_id != item.card_photo_file_id:
                new_file_ids[item.id] = file_id

    if new_file_ids:
        async with session_factory() as session:
            repo = ListingRepository(session)
            await repo.set_card_photo_file_ids(new_file_ids)
            await session.commit()

    await _safe_answer_with_keyboard(
        message=callback.message,
        text="Навигация по странице:",
        reply_markup=_result_keyboard(page, total),
    )
    await callback.answer()


def create_router(session_factory: async_sessionmaker[AsyncSession]) -> Router:
    router = Router(name="classifieds_bot")

    @router.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await _send_main_menu(
            message,
            "Привет! Это доска объявлений.\n"
            "Общей ленты нет: сначала выбираешь категорию.\n\n"
            "Команды:\n"
            "/categories - выбрать категорию и город\n"
            "/search - то же самое (быстрый вход)\n"
            "/subscribe city=... category=... - подписка (fallback)\n"
            "/unsubscribe - отключить подписку\n"
            "/help",
        )

    @router.message(Command("menu"))
    async def menu_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        await _send_main_menu(message, "Главное меню:")

    @router.message(Command("cancel"))
    async def cancel_cmd(message: Message, state: FSMContext) -> None:
        await state.clear()
        await _send_main_menu(message, "Ок, действие отменено.")

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await _send_main_menu(
            message,
            "Логика работы:\n"
            "1) Нажми /categories\n"
            "2) Выбери категорию\n"
            "3) Выбери город\n"
            "4) Листай объявления по 5 шт (сначала новые)\n\n"
            "Виден только актуальный пул (до 30 дней).",
        )

    @router.message(F.text == "Категории")
    async def menu_categories_cmd(message: Message, state: FSMContext) -> None:
        await _send_category_prompt(
            message=message,
            state=state,
            session_factory=session_factory,
        )

    @router.message(F.text == "Помощь")
    async def menu_help_cmd(message: Message) -> None:
        await help_cmd(message)

    @router.message(F.text == "Подписки")
    async def menu_subscriptions_cmd(message: Message) -> None:
        if message.from_user is None:
            await _send_main_menu(message, "Не удалось определить пользователя.")
            return

        async with session_factory() as session:
            sub_repo = SubscriptionRepository(session)
            active = await sub_repo.list_for_chat(
                telegram_user_id=message.from_user.id,
                chat_id=message.chat.id,
            )

        if not active:
            await _send_main_menu(
                message,
                "У тебя пока нет активных подписок.\n"
                "Открой Категории и нажми 'Подписаться на этот фильтр'.",
            )
            return

        lines = ["Твои активные подписки:"]
        for idx, sub in enumerate(active, start=1):
            filters = SubscriptionFilters.from_json(sub.filters_json)
            lines.append(
                f"{idx}) город: {filters.city or 'любой'}, категория: {filters.category or 'любая'}"
            )

        await message.answer(
            "\n".join(lines),
            reply_markup=_subscriptions_keyboard(),
        )

    @router.message(Command("categories"))
    async def categories_cmd(message: Message, state: FSMContext) -> None:
        await _send_category_prompt(
            message=message,
            state=state,
            session_factory=session_factory,
        )

    @router.message(Command("search"))
    async def search_cmd(message: Message, state: FSMContext) -> None:
        await _send_category_prompt(
            message=message,
            state=state,
            session_factory=session_factory,
        )

    @router.callback_query(BoardState.choosing_category)
    async def choose_category_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None:
            await callback.answer()
            return
        if not callback.data or not callback.data.startswith("cat:"):
            await callback.answer()
            return

        idx_raw = callback.data.split(":", 1)[1]
        if not idx_raw.isdigit():
            await callback.answer()
            return

        data = await state.get_data()
        categories: list[str] = data.get("categories", [])
        idx = int(idx_raw)
        if idx < 0 or idx >= len(categories):
            await callback.answer()
            return

        selected_category = categories[idx]
        await state.update_data(selected_category=selected_category)
        await state.set_state(BoardState.choosing_city)
        await callback.message.answer("Выбери город:", reply_markup=_cities_keyboard())
        await callback.answer()

    @router.callback_query(BoardState.choosing_city)
    async def choose_city_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data or not callback.data.startswith("city:"):
            await callback.answer()
            return

        city_raw = callback.data.split(":", 1)[1]
        city = None if city_raw == "any" else city_raw
        await state.update_data(selected_city=city)
        await _render_page(
            callback=callback,
            state=state,
            session_factory=session_factory,
            page=0,
        )

    @router.callback_query(BoardState.browsing)
    async def browsing_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data:
            await callback.answer()
            return

        if callback.data == "flow:categories":
            if callback.message is not None:
                await _send_category_prompt(
                    message=callback.message,
                    state=state,
                    session_factory=session_factory,
                )
            await callback.answer()
            return

        if callback.data == "flow:menu":
            await state.clear()
            if callback.message is not None:
                await callback.message.answer("Главное меню:", reply_markup=_main_menu_keyboard())
            await callback.answer()
            return

        if callback.data == "sub:current":
            if callback.from_user is None:
                await callback.answer()
                return

            data = await state.get_data()
            filters = SubscriptionFilters(
                city=data.get("selected_city"),
                category=data.get("selected_category"),
            )
            async with session_factory() as session:
                repo = SubscriptionRepository(session)
                await repo.upsert_active(
                    telegram_user_id=callback.from_user.id,
                    chat_id=callback.message.chat.id if callback.message else callback.from_user.id,
                    filters=filters,
                )
                await session.commit()

            await callback.answer("Подписка сохранена", show_alert=False)
            if callback.message is not None:
                await callback.message.answer(
                    "Подписка активирована.\n"
                    f"Город: {filters.city or 'любой'}\n"
                    f"Категория: {filters.category or 'любая'}"
                )
            return

        if callback.data.startswith("detail:"):
            listing_id_raw = callback.data.split(":", 1)[1]
            if not listing_id_raw.isdigit():
                await callback.answer()
                return

            listing_id = int(listing_id_raw)
            async with session_factory() as session:
                repo = ListingRepository(session)
                listing = await repo.get_by_id(listing_id)

            if listing is None:
                await callback.answer("Объявление не найдено", show_alert=True)
                return

            await callback.message.answer(format_listing_full(listing))
            await callback.answer()
            return

        if callback.data.startswith("page:"):
            page_raw = callback.data.split(":", 1)[1]
            if page_raw == "stay":
                await callback.answer()
                return
            if not page_raw.isdigit():
                await callback.answer()
                return

            page = int(page_raw)
            await _render_page(
                callback=callback,
                state=state,
                session_factory=session_factory,
                page=page,
            )
            return

        await callback.answer()

    @router.message(Command("subscribe"))
    async def subscribe_cmd(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не удалось определить пользователя.")
            return

        raw = (message.text or "").replace("/subscribe", "", 1).strip()
        filters = parse_subscribe_args(raw)

        async with session_factory() as session:
            repo = SubscriptionRepository(session)
            sub = await repo.upsert_active(
                telegram_user_id=message.from_user.id,
                chat_id=message.chat.id,
                filters=filters,
            )
            await session.commit()

        await message.answer(
            "Подписка активирована.\n"
            f"Город: {filters.city or 'любой'}\n"
            f"Категория: {filters.category or 'любая'}\n"
            f"ID подписки: {sub.id}",
            reply_markup=_main_menu_keyboard(),
        )

    @router.message(Command("unsubscribe"))
    async def unsubscribe_cmd(message: Message) -> None:
        if message.from_user is None:
            await message.answer("Не удалось определить пользователя.")
            return

        async with session_factory() as session:
            repo = SubscriptionRepository(session)
            changed = await repo.deactivate(
                telegram_user_id=message.from_user.id,
                chat_id=message.chat.id,
            )
            await session.commit()

        if changed:
            await _send_main_menu(message, "Подписка отключена.")
        else:
            await _send_main_menu(message, "Активной подписки не найдено.")

    @router.callback_query(F.data == "sub:off_all")
    async def subscriptions_off_all_cb(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.message is None:
            await callback.answer()
            return

        async with session_factory() as session:
            sub_repo = SubscriptionRepository(session)
            count = await sub_repo.deactivate_all_for_chat(
                telegram_user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
            )
            await session.commit()

        await callback.answer()
        await callback.message.answer(
            f"Отключено подписок: {count}",
            reply_markup=_main_menu_keyboard(),
        )

    return router
