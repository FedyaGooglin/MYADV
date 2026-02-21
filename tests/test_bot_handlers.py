from classifieds_hub.bot.handlers import parse_subscribe_args


def test_parse_subscribe_args_both_filters() -> None:
    filters = parse_subscribe_args("city=Aykhal category=Недвижимость")
    assert filters.city == "Aykhal"
    assert filters.category == "Недвижимость"


def test_parse_subscribe_args_empty() -> None:
    filters = parse_subscribe_args("")
    assert filters.city is None
    assert filters.category is None
