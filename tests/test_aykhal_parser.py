from datetime import UTC
from decimal import Decimal

from classifieds_hub.collectors.aykhal import AykhalCollector
from classifieds_hub.core.config import Settings


def test_parse_board_refs_orders_newest_first() -> None:
    html = """
    <html><body>
      <a href="/board/read100800.html">one</a>
      <a href="/board/read100811.html">two</a>
      <a href="/board/read100801.html">three</a>
      <a href="/board/read100811.html">duplicate</a>
    </body></html>
    """
    collector = AykhalCollector(Settings())
    refs = collector.parse_board_refs(html)

    assert [item.external_id for item in refs] == ["100811", "100801", "100800"]


def test_parse_listing_detail_extracts_core_fields() -> None:
    html = """
    <html>
      <body>
        <a href="/board">Объявления</a>
        <a href="/board/102">Недвижимость</a>
        <h2>Продам 2-комнатную квартиру</h2>
        <div class="col-md-8">
          <ul class="blog-info">
            <li><i class="fa fa-calendar"></i> 20.02.2026</li>
            <li><i class="fa fa-map-marker"></i> <a href="/board/city/x">Айхал</a></li>
            <li>2 400 000 <i class="fa fa-rub"></i></li>
            <li><a href="/users/test"><i class="fa fa-user"></i> Test User</a></li>
          </ul>
          <p>Теплая квартира в центре поселка.</p>
        </div>
        <div>+7 924 111-22-33</div>
        <img src="/uploads/test-photo.jpg" />
      </body>
    </html>
    """
    collector = AykhalCollector(Settings())
    ref = collector.parse_board_refs('<a href="/board/read100801.html">x</a>')[0]

    parsed = collector.parse_listing_detail(html, ref)

    assert parsed.title == "Продам 2-комнатную квартиру"
    assert parsed.description == "Теплая квартира в центре поселка."
    assert parsed.city == "Aykhal"
    assert parsed.category == "Недвижимость"
    assert parsed.author_name == "Test User"
    assert parsed.phone == "+79241112233"
    assert parsed.published_at is not None
    assert parsed.published_at.tzinfo is UTC
    assert parsed.price_value == Decimal("2400000.00")
    assert parsed.media_urls
    assert parsed.media_urls[0].startswith("https://aykhal.info/")
