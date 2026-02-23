from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from classifieds_hub.bot import delivery


def _fake_listing(idx: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=idx,
        published_at=datetime.now(UTC),
        price_text="1200000",
        city="Aykhal",
        category="Недвижимость",
        title=f"Объявление #{idx}",
        description=("Длинное описание " * 40).strip(),
        url=f"https://t.me/uda4niy/{idx}",
    )


def test_chunk_delivery_items_splits_long_batches() -> None:
    items = [_fake_listing(i) for i in range(1, 31)]
    chunks = delivery._chunk_delivery_items(items)

    assert len(chunks) > 1
    assert sum(len(chunk_items) for chunk_items, _ in chunks) == len(items)
    for chunk_items, chunk_body in chunks:
        assert chunk_items
        assert chunk_body
        assert len(chunk_body) + len(delivery.MESSAGE_PREFIX) <= delivery.MAX_TELEGRAM_MESSAGE_LEN


def test_chunk_delivery_items_truncates_oversized_block(monkeypatch) -> None:
    monkeypatch.setattr(delivery, "format_listing_extended", lambda item: "X" * 10000)

    chunks = delivery._chunk_delivery_items([object()])
    assert len(chunks) == 1

    _, body = chunks[0]
    assert len(body) + len(delivery.MESSAGE_PREFIX) <= delivery.MAX_TELEGRAM_MESSAGE_LEN
    assert body.endswith("...[сообщение сокращено]")
