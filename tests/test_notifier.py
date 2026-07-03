from decimal import Decimal

from adapters.base import Product, Status, StockResult
from notifier import (
    COLOR_INFO,
    COLOR_OVER_PRICE,
    COLOR_RESTOCK,
    COLOR_SYSTEM,
    build_heartbeat_embed,
    build_over_price_embed,
    build_restock_embed,
    build_system_embed,
)

PRODUCT = Product(
    name="Prismatic Evolutions ETB", retailer="bestbuy",
    url="https://www.bestbuy.ca/en-ca/product/x", max_price=Decimal("64.99"), sku="123",
)


def test_restock_embed():
    result = StockResult(status=Status.IN_STOCK, price=Decimal("64.99"))
    embed = build_restock_embed(PRODUCT, result)
    assert embed["color"] == COLOR_RESTOCK
    assert embed["url"] == PRODUCT.url
    assert "Prismatic Evolutions ETB" in embed["title"]
    assert "64.99" in embed["description"]


def test_over_price_embed_with_price():
    result = StockResult(status=Status.IN_STOCK, price=Decimal("89.99"))
    embed = build_over_price_embed(PRODUCT, result)
    assert embed["color"] == COLOR_OVER_PRICE
    assert "89.99" in embed["description"]


def test_over_price_embed_unknown_price():
    result = StockResult(status=Status.IN_STOCK, price=None)
    embed = build_over_price_embed(PRODUCT, result)
    assert "price unknown" in embed["description"]


def test_system_embed():
    embed = build_system_embed("walmart is blocking checks")
    assert embed["color"] == COLOR_SYSTEM
    assert "walmart is blocking checks" in embed["description"]


def test_heartbeat_embed():
    embed = build_heartbeat_embed(12, [])
    assert embed["color"] == COLOR_INFO
    assert "12" in embed["description"]
    unhealthy = build_heartbeat_embed(12, ["walmart"])
    assert "walmart" in unhealthy["description"]
