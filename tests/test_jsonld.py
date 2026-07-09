import asyncio
from decimal import Decimal

import httpx
import pytest

import adapters.jsonld as jsonld_module
from adapters.base import Blocked, Product, Status, StockResult
from adapters.jsonld import JsonLdAdapter, parse_stock_from_html

IN_STOCK_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Pokemon TCG Prismatic Evolutions ETB",
 "offers":{"@type":"Offer","price":"64.99","priceCurrency":"CAD",
 "availability":"https://schema.org/InStock"}}
</script>
</head><body><button>Add to Cart</button></body></html>
"""

OUT_OF_STOCK_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"ETB","offers":[{"price":64.99,
 "availability":"http://schema.org/OutOfStock"}]}
</script>
</head><body>Sold out</body></html>
"""

NESTED_GRAPH_HTML = """
<html><head>
<script type="application/ld+json">
{"@graph":[{"@type":"WebPage"},{"@type":"Product","name":"ETB",
 "offers":{"price":"59.99","availability":"https://schema.org/InStock"}}]}
</script>
</head></html>
"""

NO_LDJSON_OOS_HTML = "<html><body><p>This item is currently SOLD OUT</p></body></html>"
NO_LDJSON_ATC_HTML = "<html><body><button id='atc'>Add to cart</button></body></html>"
NO_SIGNAL_HTML = "<html><body><p>welcome</p></body></html>"
BAD_JSON_HTML = '<html><script type="application/ld+json">{not json</script><body>Add to cart</body></html>'


def test_parses_in_stock_offer():
    r = parse_stock_from_html(IN_STOCK_HTML, "https://x/p")
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.99")
    assert r.title == "Pokemon TCG Prismatic Evolutions ETB"
    assert r.url == "https://x/p"


def test_parses_out_of_stock_offer_list():
    r = parse_stock_from_html(OUT_OF_STOCK_HTML)
    assert r.status is Status.OUT_OF_STOCK


def test_finds_product_inside_graph():
    r = parse_stock_from_html(NESTED_GRAPH_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("59.99")


def test_fallback_out_of_stock_marker():
    assert parse_stock_from_html(NO_LDJSON_OOS_HTML).status is Status.OUT_OF_STOCK


def test_fallback_add_to_cart_marker_has_no_price():
    r = parse_stock_from_html(NO_LDJSON_ATC_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price is None


def test_no_signal_is_unknown():
    assert parse_stock_from_html(NO_SIGNAL_HTML).status is Status.UNKNOWN


def test_malformed_json_falls_back_to_markers():
    assert parse_stock_from_html(BAD_JSON_HTML).status is Status.IN_STOCK


TYPE_LIST_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":["Product","Thing"],"name":"ETB",
 "offers":{"price":"64.99","availability":"https://schema.org/InStock"}}
</script>
</head></html>
"""

NAN_PRICE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"ETB",
 "offers":{"price":"NaN","availability":"https://schema.org/InStock"}}
</script>
</head></html>
"""


def test_deeply_nested_jsonld_does_not_crash():
    # Loop-construct ~3000 levels of nesting. Built as a string because
    # json.dumps itself hits RecursionError on a dict this deep.
    blob = '{"x": ' * 3000 + '{"@type": "Product"}' + "}" * 3000
    html = (
        '<html><head><script type="application/ld+json">'
        + blob
        + "</script></head><body><p>welcome</p></body></html>"
    )
    result = parse_stock_from_html(html)
    assert result.status is Status.UNKNOWN


def test_type_list_is_recognized_as_product():
    r = parse_stock_from_html(TYPE_LIST_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.99")


def test_non_finite_price_becomes_none():
    r = parse_stock_from_html(NAN_PRICE_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price is None


PRODUCT = Product(
    name="Pokemon TCG Booster Bundle", retailer="ebgames",
    url="https://www.ebgames.ca/p/x", max_price=Decimal("34.99"),
)


def test_adapter_falls_back_to_browser_on_403(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(403, text="forbidden", request=httpx.Request("GET", url))

    async def fake_fetch(url, profile, **kw):
        assert profile == "ebgames-profile"
        return IN_STOCK_HTML

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(jsonld_module, "fetch_page_html", fake_fetch)

    async def main():
        async with httpx.AsyncClient() as client:
            return await JsonLdAdapter().check(client, PRODUCT)

    result = asyncio.run(main())
    assert result.status is Status.IN_STOCK
    assert result.price == Decimal("64.99")


def test_adapter_raises_blocked_when_browser_fallback_is_challenge_page(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(403, text="forbidden", request=httpx.Request("GET", url))

    async def fake_fetch(url, profile, **kw):
        return "<html><body>Just a moment...</body></html>"

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(jsonld_module, "fetch_page_html", fake_fetch)

    async def main():
        async with httpx.AsyncClient() as client:
            return await JsonLdAdapter().check(client, PRODUCT)

    with pytest.raises(Blocked):
        asyncio.run(main())


def _ebg_product():
    return Product(name="x", retailer="ebgames", url="https://x", max_price=Decimal("1"))


def test_browser_first_skips_httpx(monkeypatch):
    adapter = JsonLdAdapter(browser_first=True)

    async def fake_browser(product):
        return StockResult(status=Status.IN_STOCK, price=Decimal("9"), title="T", url=product.url)

    monkeypatch.setattr(adapter, "_check_via_browser", fake_browser)

    class Client:
        async def get(self, url):
            raise AssertionError("httpx must not be used when browser_first=True")

    result = asyncio.run(adapter.check(Client(), _ebg_product()))
    assert result.status is Status.IN_STOCK and result.title == "T"


def test_default_uses_httpx_first():
    adapter = JsonLdAdapter()  # browser_first defaults False
    calls = []

    class Resp:
        text = "<html></html>"
        url = "https://x"
        status_code = 200
        def raise_for_status(self):
            pass

    class Client:
        async def get(self, url):
            calls.append(url)
            return Resp()

    asyncio.run(adapter.check(Client(), _ebg_product()))
    assert calls == ["https://x"]  # httpx path was taken
