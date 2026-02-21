from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from classifieds_hub.bot.formatting import format_post_for_telegram
from classifieds_hub.bot.media import _black_square_jpeg, _to_square_jpeg


def test_format_post_for_telegram_two_lines_and_truncation() -> None:
    post = SimpleNamespace(
        title="Очень длинный заголовок " * 10,
        description="Очень длинное описание " * 20,
    )

    caption = format_post_for_telegram(post, title_limit=30, description_limit=40)
    lines = caption.split("\n")

    assert len(lines) == 2
    assert len(lines[0]) <= 30
    assert len(lines[1]) <= 40
    assert lines[0].endswith("...")
    assert lines[1].endswith("...")


def test_format_post_for_telegram_normalizes_whitespace() -> None:
    post = SimpleNamespace(title="  Заголовок   с   пробелами  ", description="\n\nОписание\t\tтут\n")
    caption = format_post_for_telegram(post)
    assert caption == "Заголовок с пробелами\nОписание тут"


def test_media_helpers_produce_square_images() -> None:
    black = _black_square_jpeg()
    with Image.open(BytesIO(black)) as img:
        assert img.size == (1080, 1080)

    source = Image.new("RGB", (1400, 800), 255)
    buf = BytesIO()
    source.save(buf, format="JPEG")
    square = _to_square_jpeg(buf.getvalue())
    assert square is not None
    with Image.open(BytesIO(square)) as img2:
        assert img2.size == (1080, 1080)
