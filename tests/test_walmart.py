import json
from decimal import Decimal

from adapters.base import Status
from adapters.walmart import parse_next_data


def make_html(availability="IN_STOCK", price=64.97, name="Pokemon ETB"):
    payload = {
        "props": {
            "pageProps": {
                "initialData": {
                    "data": {
                        "product": {
                            "name": name,
                            "availabilityStatus": availability,
                            "priceInfo": {"currentPrice": {"price": price}},
                        }
                    }
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></html>"
    )


def test_in_stock():
    r = parse_next_data(make_html(), "https://walmart.ca/p")
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.97")
    assert r.title == "Pokemon ETB"
    assert r.url == "https://walmart.ca/p"


def test_out_of_stock():
    r = parse_next_data(make_html(availability="OUT_OF_STOCK"))
    assert r.status is Status.OUT_OF_STOCK


def test_missing_next_data_is_unknown():
    assert parse_next_data("<html>no data</html>").status is Status.UNKNOWN


def test_unexpected_shape_is_unknown():
    html = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{}}</script></html>'
    assert parse_next_data(html).status is Status.UNKNOWN


def test_non_finite_price_becomes_none():
    r = parse_next_data(make_html(price="NaN"))
    assert r.status is Status.IN_STOCK
    assert r.price is None


def wrap_payload(payload):
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></html>"
    )


def make_product_payload(product):
    return {"props": {"pageProps": {"initialData": {"data": {"product": product}}}}}


def test_null_product_is_unknown():
    html = wrap_payload(make_product_payload(None))
    assert parse_next_data(html).status is Status.UNKNOWN


def test_non_dict_price_info_gives_price_none():
    product = {
        "name": "Pokemon ETB",
        "availabilityStatus": "IN_STOCK",
        "priceInfo": [1, 2],
    }
    r = parse_next_data(wrap_payload(make_product_payload(product)))
    assert r.status is Status.IN_STOCK
    assert r.price is None


def test_non_dict_current_price_gives_price_none():
    product = {
        "name": "Pokemon ETB",
        "availabilityStatus": "IN_STOCK",
        "priceInfo": {"currentPrice": "oops"},
    }
    r = parse_next_data(wrap_payload(make_product_payload(product)))
    assert r.status is Status.IN_STOCK
    assert r.price is None


def test_reversed_script_attribute_order_parses():
    product = {
        "name": "Pokemon ETB",
        "availabilityStatus": "IN_STOCK",
        "priceInfo": {"currentPrice": {"price": 64.97}},
    }
    html = (
        '<html><script type="application/json" id="__NEXT_DATA__">'
        + json.dumps(make_product_payload(product))
        + "</script></html>"
    )
    r = parse_next_data(html)
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.97")
