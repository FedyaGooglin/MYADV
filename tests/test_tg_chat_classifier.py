from __future__ import annotations

from classifieds_hub.collectors.tg_chat import classify_message_text, normalize_phone, pick_title


def test_normalize_phone() -> None:
    assert normalize_phone("8 (924) 111-22-33") == "+79241112233"
    assert normalize_phone("+7 924 111 22 33") == "+79241112233"
    assert normalize_phone("12345") is None


def test_strict_classifier_marks_candidate() -> None:
    text = "Продам 2к квартиру в Айхале, 2 500 000 руб, звонить 8 924 111-22-33"
    out = classify_message_text(text, has_media=True, strict=True)
    assert out.is_candidate is True
    assert out.city == "Aykhal"
    assert out.category == "Недвижимость"
    assert out.phone == "+79241112233"


def test_strict_classifier_rejects_small_talk() -> None:
    text = "Всем привет, кто сегодня идет на каток?"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.is_candidate is False


def test_classifier_maps_taxi_to_services() -> None:
    text = "Такси в аэропорт и из аэропорта, звоните 8 924 000 11 22"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Услуги"


def test_classifier_maps_found_keys_to_lost_and_found() -> None:
    text = "Найдены ключи возле 31 дома, обращаться по телефону 8 924 111 22 33"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Потери, находки"


def test_classifier_maps_carpool_to_poputchik() -> None:
    text = "Удачный Айхал, возьму попутчика, выезд в 20:00"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Ищу попутчика"


def test_classifier_maps_machine_route_to_carpool() -> None:
    text = "На завтра 07.02.2026 едит машина на Айхал. Пишите в личку 89241724473"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Ищу попутчика"
    assert out.is_candidate is True


def test_classifier_maps_need_machine_request_to_carpool() -> None:
    text = "Нужна машина Айхал - Удачный 89141016086"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Ищу попутчика"
    assert out.is_candidate is True


def test_classifier_keeps_otdam_as_goods_ad() -> None:
    text = "Отдам пакет вещей на девочку 134, 140 рост. Вещи хорошие, без фото."
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Товары"
    assert out.is_candidate is True


def test_classifier_keeps_prodajotsya_as_goods_ad() -> None:
    text = "Продаётся Сайга Тактика 4-3 12/76. Подробнее в личку."
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Товары"
    assert out.is_candidate is True


def test_classifier_keeps_price_only_goods_ad() -> None:
    text = "Сандалии ортопедические 31 размер, натуральная кожа. 400р"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Товары"
    assert out.is_candidate is True


def test_classifier_maps_short_buy_request_to_goods() -> None:
    text = "Куплю чехол на айфон 13 pro max, писать в лс"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Товары"
    assert out.is_candidate is True


def test_classifier_maps_vaz_to_auto() -> None:
    text = "Куплю ваз 2107, писать в лс или звонить 8 924 111-22-33"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Авто"
    assert out.is_candidate is True


def test_classifier_maps_auto_service_to_services() -> None:
    text = "Установка автосигнализаций и ремонт авто в Удачном, звоните 89240000000"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Услуги"
    assert out.is_candidate is True


def test_classifier_keeps_appliance_machine_in_goods() -> None:
    text = "Продам посудомоечную машину BOSCH, 15000, писать в лс"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Товары"
    assert out.is_candidate is True


def test_classifier_maps_balok_to_realty() -> None:
    text = "Продаю балок, цена договорная, писать в лс"
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Недвижимость"
    assert out.is_candidate is True


def test_classifier_does_not_map_two_cities_to_carpool_without_route_intent() -> None:
    text = (
        "Куплю темно-бордовый ваз 2107 (карбюратор). "
        "Предложения по номеру +79244605550. Айхал, Удачный, Мирный."
    )
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category == "Авто"
    assert out.is_candidate is True


def test_classifier_does_not_map_news_about_city_to_carpool() -> None:
    text = (
        "На сегодняшний день место обрыва кабеля установлено. "
        "Ведутся работы на участке между Айхалом и Моркокой."
    )
    out = classify_message_text(text, has_media=False, strict=True)
    assert out.category != "Ищу попутчика"
    assert out.is_candidate is False


def test_classifier_rejects_bright_winter_small_talk() -> None:
    text = (
        "Кто сказал, что зима в Удачном скучная? "
        "Это самый яркий сезон, зажигаем в алмазной столице!"
    )
    out = classify_message_text(text, has_media=True, strict=True)
    assert out.is_candidate is False


def test_pick_title_strips_city_prefix_and_description_label() -> None:
    text = "Описание: г.Удачный Аренда гаража для мероприятий\nБольшой бильярдный стол"
    title = pick_title(text, 123)
    assert title.startswith("Аренда гаража")
