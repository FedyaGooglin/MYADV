from __future__ import annotations

import asyncio
from collections.abc import Sequence
from aiogram import Router
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classifieds_hub.bot.formatting import format_listing_full
from classifieds_hub.core.config import Settings
from classifieds_hub.db.repository import (
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
            [InlineKeyboardButton(text="⬅️ К категориям", callback_data="flow:categories")],
            [InlineKeyboardButton(text="В меню", callback_data="flow:menu")],
        ]
    )


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Категории", callback_data="menu:categories")],
            [InlineKeyboardButton(text="Подписки", callback_data="menu:subscriptions")],
            [InlineKeyboardButton(text="Помощь", callback_data="menu:help")],
        ]
    )


def _subscriptions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отключить все подписки", callback_data="sub:off_all")],
            [InlineKeyboardButton(text="Категории", callback_data="flow:categories")],
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


async def _safe_edit_with_keyboard(
    *,
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> bool:
    payload = text
    for attempt in range(3):
        try:
            await message.edit_text(payload, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "message is not modified" in lowered:
                return True
            if "message is too long" in lowered:
                payload = payload[:3500].rstrip() + "\n\n[Сообщение сокращено]"
                continue
            if "message can't be edited" in lowered or "message to edit not found" in lowered:
                return False
            raise
        except TelegramNetworkError:
            if attempt == 2:
                return False
            await asyncio.sleep(0.8 * (attempt + 1))
    return False


async def _show_screen(
    *,
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    prefer_edit: bool,
) -> None:
    if prefer_edit:
        edited = await _safe_edit_with_keyboard(
            message=message,
            text=text,
            reply_markup=reply_markup,
        )
        if edited:
            return

    await _safe_answer_with_keyboard(
        message=message,
        text=text,
        reply_markup=reply_markup,
    )


async def _safe_callback_answer(callback: CallbackQuery) -> None:
    try:
        await callback.answer()
    except (TelegramBadRequest, TelegramNetworkError):
        pass


def _categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, category in enumerate(categories):
        rows.append(
            [InlineKeyboardButton(text=category, callback_data=f"cat:{idx}")]
        )
    rows.append([InlineKeyboardButton(text="В меню", callback_data="flow:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_listing_button_text(*, index: int, title: str, price_text: str | None) -> str:
    compact_title = " ".join(title.split())
    if len(compact_title) > 34:
        compact_title = compact_title[:31].rstrip() + "..."

    compact_price = " ".join((price_text or "без цены").split())
    if len(compact_price) > 18:
        compact_price = compact_price[:15].rstrip() + "..."

    text = f"{index}) {compact_title} | {compact_price}"
    if len(text) > 64:
        text = text[:61].rstrip() + "..."
    return text


def _result_keyboard(
    page: int,
    total: int,
    items: Sequence[object],
    *,
    allow_city_switch: bool,
) -> InlineKeyboardMarkup:
    max_page = max(0, (total - 1) // PAGE_SIZE)
    prev_page = max(0, page - 1)
    next_page = min(max_page, page + 1)

    rows: list[list[InlineKeyboardButton]] = []
    start_idx = page * PAGE_SIZE + 1
    for offset, item in enumerate(items):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_short_listing_button_text(
                        index=start_idx + offset,
                        title=str(getattr(item, "title", "Объявление")),
                        price_text=getattr(item, "price_text", None),
                    ),
                    callback_data=f"detail:{getattr(item, 'id')}",
                )
            ]
        )

    nav_row = [
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page:{prev_page}"),
        InlineKeyboardButton(text=f"{page + 1}/{max_page + 1}", callback_data="page:stay"),
        InlineKeyboardButton(text="Дальше ➡️", callback_data=f"page:{next_page}"),
    ]

    rows.extend(
        [
            nav_row,
            [InlineKeyboardButton(text="Подписаться на этот фильтр", callback_data="sub:current")],
        ]
    )

    if allow_city_switch:
        rows.append([InlineKeyboardButton(text="Сменить город", callback_data="flow:cities")])

    rows.extend(
        [
            [InlineKeyboardButton(text="Сменить категорию", callback_data="flow:categories")],
            [InlineKeyboardButton(text="В меню", callback_data="flow:menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    prefer_edit: bool = False,
) -> None:
    async with session_factory() as session:
        repo = ListingRepository(session)
        categories = await repo.list_active_categories()

    if not categories:
        await _show_screen(
            message=message,
            text="Пока нет актуальных объявлений по категориям.",
            reply_markup=_main_menu_keyboard(),
            prefer_edit=prefer_edit,
        )
        return

    # Храним карту index->category в состоянии, чтобы callback были короткие и надежные.
    await state.clear()
    await state.set_state(BoardState.choosing_category)
    await state.update_data(categories=categories)
    await _show_screen(
        message=message,
        text="Выбери категорию:",
        reply_markup=_categories_keyboard(categories),
        prefer_edit=prefer_edit,
    )


async def _render_page(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    page: int,
) -> None:
    if callback.message is None or not isinstance(callback.message, Message):
        await _safe_callback_answer(callback)
        return
    message = callback.message
    await _safe_callback_answer(callback)

    data = await state.get_data()
    category = data.get("selected_category")
    city = data.get("selected_city")
    if category == "Ищу попутчика":
        city = None

    if not category:
        await _show_screen(
            message=message,
            text="Категория не выбрана. Нажми /categories",
            reply_markup=_main_menu_keyboard(),
            prefer_edit=True,
        )
        return

    async with session_factory() as session:
        repo = ListingRepository(session)
        items, total = await repo.list_by_category_page(
            category=category,
            city=city,
            page=page,
            page_size=PAGE_SIZE,
        )

    if total == 0 or not items:
        fallback_keyboard = (
            _result_keyboard(
                page=0,
                total=0,
                items=[],
                allow_city_switch=category != "Ищу попутчика",
            )
            if category
            else _main_menu_keyboard()
        )
        await _show_screen(
            message=message,
            text="По выбранному фильтру нет актуальных объявлений.",
            reply_markup=fallback_keyboard,
            prefer_edit=True,
        )
        return

    city_title = "Все города" if category == "Ищу попутчика" else (city or "Любой")

    await state.set_state(BoardState.browsing)
    await state.update_data(current_page=page)

    header = (
        f"Категория: {category}\n"
        f"Город: {city_title}\n"
        f"Найдено объявлений: {total}"
    )

    await _show_screen(
        message=message,
        text=header,
        reply_markup=_result_keyboard(
            page,
            total,
            items,
            allow_city_switch=category != "Ищу попутчика",
        ),
        prefer_edit=True,
    )


def create_router(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> Router:
    router = Router(name="classifieds_bot")

    async def show_subscriptions_screen(
        *,
        message: Message,
        telegram_user_id: int,
        chat_id: int,
        prefer_edit: bool,
    ) -> None:
        async with session_factory() as session:
            sub_repo = SubscriptionRepository(session)
            active = await sub_repo.list_for_chat(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
            )

        if not active:
            await _show_screen(
                message=message,
                text=(
                    "У тебя пока нет активных подписок.\n"
                    "Открой Категории и нажми 'Подписаться на этот фильтр'."
                ),
                reply_markup=_main_menu_keyboard(),
                prefer_edit=prefer_edit,
            )
            return

        lines = ["Твои активные подписки:"]
        for idx, sub in enumerate(active, start=1):
            filters = SubscriptionFilters.from_json(sub.filters_json)
            lines.append(
                f"{idx}) город: {filters.city or 'любой'}, категория: {filters.category or 'любая'}"
            )

        await _show_screen(
            message=message,
            text="\n".join(lines),
            reply_markup=_subscriptions_keyboard(),
            prefer_edit=prefer_edit,
        )

    @router.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await _send_main_menu(
            message,
            "Привет! Это доска объявлений.\n"
            "Выбирай раздел через кнопки ниже.",
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
            "1) Нажми Категории\n"
            "2) Выбери категорию\n"
            "3) Выбери город\n"
            "4) Листай объявления по 5 шт (сначала новые)\n\n"
            "Виден только актуальный пул (до 30 дней).",
        )

    @router.callback_query(F.data == "menu:categories")
    async def menu_categories_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return
        await _safe_callback_answer(callback)
        await _send_category_prompt(
            message=callback.message,
            state=state,
            session_factory=session_factory,
            prefer_edit=True,
        )

    @router.callback_query(F.data == "menu:help")
    async def menu_help_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return
        await _safe_callback_answer(callback)
        await state.clear()
        await _show_screen(
            message=callback.message,
            text=(
                "Логика работы:\n"
                "1) Выбери Категории\n"
                "2) Выбери категорию\n"
                "3) Выбери город\n"
                "4) Листай объявления по 5 шт\n\n"
                "Для Ищу попутчика город объединен в одну ленту."
            ),
            reply_markup=_main_menu_keyboard(),
            prefer_edit=True,
        )

    @router.callback_query(F.data == "menu:subscriptions")
    async def menu_subscriptions_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return
        if callback.from_user is None:
            await _safe_callback_answer(callback)
            return
        await _safe_callback_answer(callback)
        await state.clear()
        await show_subscriptions_screen(
            message=callback.message,
            telegram_user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            prefer_edit=True,
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
        await show_subscriptions_screen(
            message=message,
            telegram_user_id=message.from_user.id,
            chat_id=message.chat.id,
            prefer_edit=False,
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
        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return
        message = callback.message

        await _safe_callback_answer(callback)

        if callback.data == "flow:menu":
            await state.clear()
            await _show_screen(
                message=message,
                text="Главное меню:",
                reply_markup=_main_menu_keyboard(),
                prefer_edit=True,
            )
            return

        if not callback.data or not callback.data.startswith("cat:"):
            return

        idx_raw = callback.data.split(":", 1)[1]
        if not idx_raw.isdigit():
            return

        data = await state.get_data()
        categories: list[str] = data.get("categories", [])
        idx = int(idx_raw)
        if idx < 0 or idx >= len(categories):
            return

        selected_category = categories[idx]
        if selected_category == "Ищу попутчика":
            await state.update_data(selected_category=selected_category, selected_city=None)
            await state.set_state(BoardState.browsing)
            await _render_page(
                callback=callback,
                state=state,
                session_factory=session_factory,
                settings=settings,
                page=0,
            )
            return

        await state.update_data(selected_category=selected_category)
        await state.set_state(BoardState.choosing_city)
        await _show_screen(
            message=message,
            text=f"Категория: {selected_category}\nВыбери город:",
            reply_markup=_cities_keyboard(),
            prefer_edit=True,
        )

    @router.callback_query(BoardState.choosing_city)
    async def choose_city_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return

        message = callback.message
        await _safe_callback_answer(callback)

        if callback.data == "flow:menu":
            await state.clear()
            await _show_screen(
                message=message,
                text="Главное меню:",
                reply_markup=_main_menu_keyboard(),
                prefer_edit=True,
            )
            return

        if callback.data == "flow:categories":
            await _send_category_prompt(
                message=message,
                state=state,
                session_factory=session_factory,
                prefer_edit=True,
            )
            return

        if not callback.data or not callback.data.startswith("city:"):
            return

        city_raw = callback.data.split(":", 1)[1]
        city = None if city_raw == "any" else city_raw
        await state.update_data(selected_city=city)
        await _render_page(
            callback=callback,
            state=state,
            session_factory=session_factory,
            settings=settings,
            page=0,
        )

    @router.callback_query(BoardState.browsing)
    async def browsing_cb(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data:
            await _safe_callback_answer(callback)
            return

        if callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return
        message = callback.message
        await _safe_callback_answer(callback)

        if callback.data == "menu:categories":
            await _send_category_prompt(
                message=message,
                state=state,
                session_factory=session_factory,
                prefer_edit=True,
            )
            return

        if callback.data == "menu:help":
            await state.clear()
            await _show_screen(
                message=message,
                text=(
                    "Логика работы:\n"
                    "1) Выбери Категории\n"
                    "2) Выбери категорию\n"
                    "3) Выбери город\n"
                    "4) Листай объявления по 5 шт\n\n"
                    "Для Ищу попутчика город объединен в одну ленту."
                ),
                reply_markup=_main_menu_keyboard(),
                prefer_edit=True,
            )
            return

        if callback.data == "menu:subscriptions":
            if callback.from_user is None:
                return
            await state.clear()
            await show_subscriptions_screen(
                message=message,
                telegram_user_id=callback.from_user.id,
                chat_id=message.chat.id,
                prefer_edit=True,
            )
            return

        if callback.data == "flow:categories":
            await _send_category_prompt(
                message=message,
                state=state,
                session_factory=session_factory,
                prefer_edit=True,
            )
            return

        if callback.data == "flow:menu":
            await state.clear()
            await _show_screen(
                message=message,
                text="Главное меню:",
                reply_markup=_main_menu_keyboard(),
                prefer_edit=True,
            )
            return

        if callback.data == "flow:cities":
            data = await state.get_data()
            if data.get("selected_category") == "Ищу попутчика":
                await _render_page(
                    callback=callback,
                    state=state,
                    session_factory=session_factory,
                    settings=settings,
                    page=int(data.get("current_page", 0) or 0),
                )
                return

            await state.set_state(BoardState.choosing_city)
            await _show_screen(
                message=message,
                text=f"Категория: {data.get('selected_category') or '-'}\nВыбери город:",
                reply_markup=_cities_keyboard(),
                prefer_edit=True,
            )
            return

        if callback.data == "sub:current":
            if callback.from_user is None:
                return

            data = await state.get_data()
            category = data.get("selected_category")
            filters = SubscriptionFilters(
                city=(None if category == "Ищу попутчика" else data.get("selected_city")),
                category=category,
            )
            async with session_factory() as session:
                repo = SubscriptionRepository(session)
                await repo.upsert_active(
                    telegram_user_id=callback.from_user.id,
                    chat_id=message.chat.id,
                    filters=filters,
                )
                await session.commit()

            await message.answer(
                "Подписка активирована.\n"
                f"Город: {filters.city or 'любой'}\n"
                f"Категория: {filters.category or 'любая'}"
            )
            return

        if callback.data.startswith("detail:"):
            listing_id_raw = callback.data.split(":", 1)[1]
            if not listing_id_raw.isdigit():
                return

            listing_id = int(listing_id_raw)
            async with session_factory() as session:
                repo = ListingRepository(session)
                listing = await repo.get_by_id(listing_id)

            if listing is None:
                await callback.answer("Объявление не найдено", show_alert=True)
                return

            await message.answer(format_listing_full(listing))
            return

        if callback.data.startswith("page:"):
            page_raw = callback.data.split(":", 1)[1]
            if page_raw == "stay":
                return
            if not page_raw.isdigit():
                return

            page = int(page_raw)
            await _render_page(
                callback=callback,
                state=state,
                session_factory=session_factory,
                settings=settings,
                page=page,
            )
            return

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
        if callback.from_user is None or callback.message is None or not isinstance(callback.message, Message):
            await _safe_callback_answer(callback)
            return

        async with session_factory() as session:
            sub_repo = SubscriptionRepository(session)
            count = await sub_repo.deactivate_all_for_chat(
                telegram_user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
            )
            await session.commit()

        await _safe_callback_answer(callback)
        message = callback.message
        await message.answer(
            f"Отключено подписок: {count}",
            reply_markup=_main_menu_keyboard(),
        )

    return router
