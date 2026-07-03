import asyncio
from decimal import Decimal

from adapters.base import Product, Status
from adapters.bestbuy import BestBuyAdapter, parse_availability, parse_price

AVAIL_IN_STOCK = {
    "availabilities": [
        {"sku": "17095567", "shipping": {"purchasable": True, "status": "InStock"}}
    ]
}
AVAIL_SOLD_OUT = {
    "availabilities": [
        {"sku": "17095567", "shipping": {"purchasable": False, "status": "SoldOutOnline"}}
    ]
}
AVAIL_NOT_PURCHASABLE = {
    "availabilities": [
        {"sku": "17095567", "shipping": {"purchasable": False, "status": "InStock"}}
    ]
}
AVAIL_EMPTY = {"availabilities": []}

OFFERS = [{"salePrice": 64.99, "regularPrice": 69.99}]
OFFERS_NO_SALE = [{"regularPrice": 69.99}]


def test_in_stock_when_purchasable_and_instock():
    assert parse_availability(AVAIL_IN_STOCK) is Status.IN_STOCK


def test_sold_out():
    assert parse_availability(AVAIL_SOLD_OUT) is Status.OUT_OF_STOCK


def test_not_purchasable_is_out_of_stock():
    assert parse_availability(AVAIL_NOT_PURCHASABLE) is Status.OUT_OF_STOCK


def test_missing_availabilities_is_unknown():
    assert parse_availability(AVAIL_EMPTY) is Status.UNKNOWN
    assert parse_availability({}) is Status.UNKNOWN


def test_price_prefers_sale_price():
    assert parse_price(OFFERS) == Decimal("64.99")


def test_price_falls_back_to_regular():
    assert parse_price(OFFERS_NO_SALE) == Decimal("69.99")


def test_price_none_when_empty():
    assert parse_price([]) is None


def test_zero_sale_price_is_not_skipped():
    assert parse_price([{"salePrice": 0, "regularPrice": 69.99}]) == Decimal("0")


def test_malformed_availability_entry_is_unknown():
    assert parse_availability({"availabilities": ["garbage"]}) is Status.UNKNOWN


def test_check_without_sku_returns_unknown_without_http():
    product = Product(
        name="Prismatic Evolutions ETB",
        retailer="bestbuy",
        url="https://www.bestbuy.ca/en-ca/product/x",
        max_price=Decimal("80"),
        sku=None,
    )
    result = asyncio.run(BestBuyAdapter().check(None, product))
    assert result.status is Status.UNKNOWN
    assert result.price is None
    assert result.title == product.name
    assert result.url == product.url
