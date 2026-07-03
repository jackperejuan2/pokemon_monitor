from decimal import Decimal

from adapters.base import Status
from adapters.bestbuy import parse_availability, parse_price

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
