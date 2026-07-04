import asyncio
import json
from decimal import Decimal

import httpx
import pytest

import adapters.walmart as walmart_module
from adapters.base import Blocked, Product, Status
from adapters.walmart import WalmartAdapter, looks_blocked, parse_next_data

PRODUCT = Product(
    name="Pokemon ETB", retailer="walmart",
    url="https://www.walmart.ca/en/ip/x/6Z5CLX0MKJ39", max_price=Decimal("34.99"),
)


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


def test_looks_blocked_detects_blocked_redirect_url():
    assert looks_blocked("https://www.walmart.ca/blocked?url=%2Fen%2Fip%2F123")


def test_looks_blocked_detects_px_captcha_body():
    assert looks_blocked("<html><body>Please solve the px-captcha</body></html>")


def test_looks_blocked_detects_robot_or_human_body():
    assert looks_blocked("<html><body>Robot or human?</body></html>")


def test_looks_blocked_is_false_for_normal_product_page():
    assert not looks_blocked(make_html())
    assert not looks_blocked("https://www.walmart.ca/en/ip/Pokemon-ETB/6Z5CLX0MKJ39")


def test_looks_blocked_detects_bare_forbidden_body():
    assert looks_blocked("<html><body>Forbidden</body></html>")


def test_looks_blocked_detects_bare_access_denied_body():
    assert looks_blocked("<html><body>Access Denied</body></html>")


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


def test_adapter_falls_back_to_browser_on_perimeterx_redirect(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(
            200, text="<html>Robot or human?</html>",
            request=httpx.Request("GET", "https://www.walmart.ca/blocked?url=x"),
        )

    async def fake_fetch(url, profile, **kw):
        assert profile == "walmart-profile"
        return make_html()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(walmart_module, "fetch_page_html", fake_fetch)

    async def main():
        async with httpx.AsyncClient() as client:
            return await WalmartAdapter().check(client, PRODUCT)

    result = asyncio.run(main())
    assert result.status is Status.IN_STOCK
    assert result.price == Decimal("64.97")


def test_adapter_falls_back_to_browser_on_403(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(403, text="forbidden", request=httpx.Request("GET", url))

    async def fake_fetch(url, profile, **kw):
        return make_html(availability="OUT_OF_STOCK")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(walmart_module, "fetch_page_html", fake_fetch)

    async def main():
        async with httpx.AsyncClient() as client:
            return await WalmartAdapter().check(client, PRODUCT)

    result = asyncio.run(main())
    assert result.status is Status.OUT_OF_STOCK


def test_adapter_raises_blocked_when_browser_also_blocked(monkeypatch):
    async def fake_get(self, url, *a, **kw):
        return httpx.Response(403, text="forbidden", request=httpx.Request("GET", url))

    async def fake_fetch(url, profile, **kw):
        return "<html>px-captcha</html>"

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(walmart_module, "fetch_page_html", fake_fetch)

    async def main():
        async with httpx.AsyncClient() as client:
            return await WalmartAdapter().check(client, PRODUCT)

    with pytest.raises(Blocked):
        asyncio.run(main())
