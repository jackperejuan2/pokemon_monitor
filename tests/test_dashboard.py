# tests/test_dashboard.py
from datetime import datetime
from decimal import Decimal

from adapters.base import Status, StockResult
from dashboard import DashboardRecord, update_record

NOW = datetime(2026, 7, 4, 12, 0, 0)
LATER = datetime(2026, 7, 4, 13, 0, 0)


def in_stock(price):
    return StockResult(status=Status.IN_STOCK, price=Decimal(price) if price is not None else None)


OOS = StockResult(status=Status.OUT_OF_STOCK)
UNKNOWN = StockResult(status=Status.UNKNOWN)


def test_first_observation_sets_current_and_lowest_and_changes():
    rec, changed = update_record(DashboardRecord(), in_stock("100.00"), NOW)
    assert changed is True
    assert rec.last_price == "100.00"
    assert rec.last_status == "in_stock"
    assert rec.lowest_price == "100.00"
    assert rec.last_checked == NOW.isoformat()
    assert rec.last_changed == NOW.isoformat()


def test_lower_price_updates_lowest():
    prev = DashboardRecord(last_price="100.00", last_status="in_stock", lowest_price="100.00",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, in_stock("80.00"), LATER)
    assert changed is True
    assert rec.lowest_price == "80.00"
    assert rec.last_changed == LATER.isoformat()


def test_higher_price_keeps_lowest_but_still_changes():
    prev = DashboardRecord(last_price="80.00", last_status="in_stock", lowest_price="80.00",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, in_stock("120.00"), LATER)
    assert changed is True
    assert rec.last_price == "120.00"
    assert rec.lowest_price == "80.00"


def test_same_price_and_status_is_no_change():
    prev = DashboardRecord(last_price="80.00", last_status="in_stock", lowest_price="80.00",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, in_stock("80.00"), LATER)
    assert changed is False
    assert rec.last_checked == LATER.isoformat()
    assert rec.last_changed == NOW.isoformat()


def test_status_change_counts_as_change():
    prev = DashboardRecord(last_price="80.00", last_status="in_stock", lowest_price="80.00")
    rec, changed = update_record(prev, OOS, LATER)
    assert changed is True
    assert rec.last_status == "out_of_stock"


def test_unknown_keeps_everything_but_checked_time():
    prev = DashboardRecord(last_price="80.00", last_status="in_stock", lowest_price="80.00",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, UNKNOWN, LATER)
    assert changed is False
    assert rec.last_price == "80.00" and rec.last_status == "in_stock"
    assert rec.lowest_price == "80.00"
    assert rec.last_checked == LATER.isoformat()


def test_in_stock_with_no_price_does_not_set_lowest():
    prev = DashboardRecord(last_status="out_of_stock")
    rec, changed = update_record(prev, in_stock(None), NOW)
    assert changed is True
    assert rec.last_price is None
    assert rec.lowest_price is None


def test_equal_price_different_string_is_not_a_change():
    prev = DashboardRecord(last_price="19.99", last_status="in_stock", lowest_price="19.99",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, in_stock("19.990"), LATER)
    assert changed is False
    assert rec.last_changed == NOW.isoformat()


def test_in_stock_price_flake_keeps_last_known_price():
    prev = DashboardRecord(last_price="80.00", last_status="in_stock", lowest_price="80.00",
                           last_checked=NOW.isoformat(), last_changed=NOW.isoformat())
    rec, changed = update_record(prev, in_stock(None), LATER)
    assert rec.last_price == "80.00"
    assert changed is False
    assert rec.last_changed == NOW.isoformat()


def test_records_roundtrip(tmp_path, monkeypatch):
    import dashboard as d
    monkeypatch.setattr(d, "DASHBOARD_PATH", tmp_path / "dashboard_data.json")
    recs = {"bestbuy:1": d.DashboardRecord(last_price="80.00", last_status="in_stock",
                                           lowest_price="80.00")}
    d.save_records(recs)
    assert d.load_records() == recs
    assert not (tmp_path / "dashboard_data.json.tmp").exists()


def test_load_records_missing_is_empty(tmp_path, monkeypatch):
    import dashboard as d
    monkeypatch.setattr(d, "DASHBOARD_PATH", tmp_path / "nope.json")
    assert d.load_records() == {}


def test_load_records_corrupt_is_empty(tmp_path, monkeypatch):
    import dashboard as d
    p = tmp_path / "dashboard_data.json"
    p.write_text("{not json")
    monkeypatch.setattr(d, "DASHBOARD_PATH", p)
    assert d.load_records() == {}


def test_load_records_tolerates_unknown_keys(tmp_path, monkeypatch):
    import json
    import dashboard as d
    p = tmp_path / "dashboard_data.json"
    p.write_text(json.dumps({"bestbuy:1": {"last_price": "80.00", "last_status": "in_stock",
                                           "future_field": 1}}))
    monkeypatch.setattr(d, "DASHBOARD_PATH", p)
    loaded = d.load_records()
    assert loaded["bestbuy:1"].last_price == "80.00"


def _product(name, retailer, set_name, packs, max_price, url="https://x/p", sku=None):
    from decimal import Decimal
    from adapters.base import Product
    return Product(name=name, retailer=retailer, url=url, max_price=Decimal(str(max_price)),
                   sku=sku, packs=packs, set_name=set_name)


def test_render_groups_by_set_and_flags_buys():
    from datetime import datetime
    import dashboard as d
    products = [
        _product("Perfect Order Booster Box", "bestbuy", "Perfect Order", 36, 360),
        _product("Chaos Rising Booster Bundle", "bestbuy", "Chaos Rising", 6, 60,
                 url="https://x/chaos"),
    ]
    records = {
        products[0].key: d.DashboardRecord(last_price="344.99", last_status="in_stock",
                                           lowest_price="344.99"),
        products[1].key: d.DashboardRecord(last_price="103.99", last_status="in_stock",
                                           lowest_price="49.98"),
    }
    html = d.render_html(products, records, datetime(2026, 7, 4, 12, 0, 0), healthy=True)
    assert "<!DOCTYPE html>" in html
    assert "Perfect Order" in html and "Chaos Rising" in html
    assert 'class="buy"' in html
    assert "$9.58" in html
    assert "$17.33" in html
    assert "https://x/chaos" in html


def test_render_missing_price_shows_dash():
    from datetime import datetime
    import dashboard as d
    products = [_product("A Single", "bestbuy", "151", 1, 10)]
    records = {products[0].key: d.DashboardRecord(last_price=None, last_status="out_of_stock",
                                                  lowest_price="9.99")}
    html = d.render_html(products, records, datetime(2026, 7, 4, 12, 0, 0), healthy=True)
    assert "out of stock" in html.lower()
    assert "&mdash;" in html or "—" in html
    assert "$9.99" in html


def test_render_product_with_no_record_is_safe():
    from datetime import datetime
    import dashboard as d
    products = [_product("New Item", "bestbuy", "151", 9, 90)]
    html = d.render_html(products, {}, datetime(2026, 7, 4, 12, 0, 0), healthy=True)
    assert "New Item" in html


def test_render_tolerates_bad_price_string():
    from datetime import datetime
    import dashboard as d
    products = [_product("Corrupt Row", "bestbuy", "151", 9, 90)]
    records = {products[0].key: d.DashboardRecord(last_price="oops", last_status="in_stock",
                                                  lowest_price="bad")}
    html = d.render_html(products, records, datetime(2026, 7, 4, 12, 0, 0), healthy=True)
    assert isinstance(html, str)
    assert "Corrupt Row" in html
    assert "&mdash;" in html or "—" in html
