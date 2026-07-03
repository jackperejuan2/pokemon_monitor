from datetime import datetime, timedelta
from decimal import Decimal

from adapters.base import Product, Status, StockResult
from state import ProductState, decide

NOW = datetime(2026, 7, 3, 12, 0, 0)
PRODUCT = Product(
    name="ETB", retailer="bestbuy", url="https://x/p", max_price=Decimal("64.99"), sku="1"
)


def in_stock(price):
    return StockResult(status=Status.IN_STOCK, price=Decimal(price) if price else None)


OOS = StockResult(status=Status.OUT_OF_STOCK)
UNKNOWN = StockResult(status=Status.UNKNOWN)


def test_oos_to_in_stock_at_good_price_alerts_restock():
    d = decide(ProductState(status="out_of_stock"), in_stock("64.99"), PRODUCT, NOW)
    assert d.alert == "restock"
    assert d.new_state.status == "in_stock"
    assert d.new_state.price_ok is True


def test_still_in_stock_does_not_realert():
    prev = ProductState(status="in_stock", price_ok=True)
    d = decide(prev, in_stock("64.99"), PRODUCT, NOW)
    assert d.alert is None


def test_restock_realerts_after_going_oos_and_back():
    prev = ProductState(status="in_stock", price_ok=True)
    after_oos = decide(prev, OOS, PRODUCT, NOW)
    assert after_oos.alert is None
    assert after_oos.new_state.price_ok is False
    d = decide(after_oos.new_state, in_stock("60.00"), PRODUCT, NOW)
    assert d.alert == "restock"


def test_over_price_notice_first_time():
    d = decide(ProductState(status="out_of_stock"), in_stock("89.99"), PRODUCT, NOW)
    assert d.alert == "over_price"
    assert d.new_state.last_over_price_alert == NOW.isoformat()


def test_over_price_notice_rate_limited_to_daily():
    prev = ProductState(
        status="in_stock", price_ok=False, last_over_price_alert=NOW.isoformat()
    )
    within_day = decide(prev, in_stock("89.99"), PRODUCT, NOW + timedelta(hours=23))
    assert within_day.alert is None
    next_day = decide(prev, in_stock("89.99"), PRODUCT, NOW + timedelta(hours=25))
    assert next_day.alert == "over_price"


def test_price_drop_to_max_while_in_stock_alerts_restock():
    prev = ProductState(status="in_stock", price_ok=False)
    d = decide(prev, in_stock("64.99"), PRODUCT, NOW)
    assert d.alert == "restock"


def test_unknown_price_in_stock_gets_over_price_notice():
    d = decide(ProductState(status="out_of_stock"), in_stock(None), PRODUCT, NOW)
    assert d.alert == "over_price"


def test_unknown_result_keeps_previous_state_and_no_alert():
    prev = ProductState(status="in_stock", price_ok=True)
    d = decide(prev, UNKNOWN, PRODUCT, NOW)
    assert d.alert is None
    assert d.new_state.status == "in_stock"
    assert d.new_state.price_ok is True


def test_state_roundtrips_through_json(tmp_path, monkeypatch):
    import state as state_module

    monkeypatch.setattr(state_module, "STATE_PATH", tmp_path / "state.json")
    states = {"bestbuy:1": ProductState(status="in_stock", price_ok=True)}
    state_module.save_state(states)
    loaded = state_module.load_state()
    assert loaded == states


def test_load_state_empty_when_missing(tmp_path, monkeypatch):
    import state as state_module

    monkeypatch.setattr(state_module, "STATE_PATH", tmp_path / "nope.json")
    assert state_module.load_state() == {}
