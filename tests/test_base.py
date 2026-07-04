from decimal import Decimal

from adapters.base import Blocked, Product, Status, StockResult, raise_if_blocked
import httpx
import pytest


def test_product_key_uses_sku_when_present():
    p = Product(name="ETB", retailer="bestbuy", url="https://x/y", max_price=Decimal("64.99"), sku="123")
    assert p.key == "bestbuy:123"


def test_product_key_falls_back_to_url():
    p = Product(name="ETB", retailer="costco", url="https://x/y", max_price=Decimal("64.99"))
    assert p.key == "costco:https://x/y"


def test_stock_result_defaults():
    r = StockResult(status=Status.OUT_OF_STOCK)
    assert r.price is None and r.title == "" and r.url == ""


@pytest.mark.parametrize("code", [403, 429])
def test_raise_if_blocked_raises_on_block_codes(code):
    resp = httpx.Response(code, request=httpx.Request("GET", "https://www.example.ca/p"))
    with pytest.raises(Blocked):
        raise_if_blocked(resp)


def test_raise_if_blocked_passes_normal_responses():
    resp = httpx.Response(200, request=httpx.Request("GET", "https://www.example.ca/p"))
    raise_if_blocked(resp)  # no exception


def test_registry_covers_all_seven_retailers():
    from adapters import ADAPTERS

    assert set(ADAPTERS) == {
        "bestbuy", "walmart", "toysrus", "indigo", "ebgames", "costco", "pokemoncenter",
    }
    for adapter in ADAPTERS.values():
        assert callable(getattr(adapter, "check", None))
