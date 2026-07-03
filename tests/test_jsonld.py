from decimal import Decimal

from adapters.base import Status
from adapters.jsonld import parse_stock_from_html

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
