import json
from datetime import datetime
from decimal import Decimal

from adapters.base import Product
from monitor import RetailerHealth, check_interval, in_quiet_hours

CONFIG = {
    "check_interval_seconds": [120, 300],
    "pokemoncenter_interval_seconds": [600, 900],
    "quiet_hours": {"start": "01:30", "end": "07:00"},
}


def product(retailer):
    return Product(name="x", retailer=retailer, url="https://x", max_price=Decimal("1"))


def test_quiet_hours_inside():
    assert in_quiet_hours(datetime(2026, 7, 3, 3, 0), CONFIG)


def test_quiet_hours_outside():
    assert not in_quiet_hours(datetime(2026, 7, 3, 12, 0), CONFIG)


def test_quiet_hours_boundaries():
    assert in_quiet_hours(datetime(2026, 7, 3, 1, 30), CONFIG)
    assert not in_quiet_hours(datetime(2026, 7, 3, 7, 0), CONFIG)


def test_quiet_hours_wrapping_midnight():
    config = {"quiet_hours": {"start": "23:00", "end": "06:00"}}
    assert in_quiet_hours(datetime(2026, 7, 3, 23, 30), config)
    assert in_quiet_hours(datetime(2026, 7, 3, 2, 0), config)
    assert not in_quiet_hours(datetime(2026, 7, 3, 12, 0), config)


def test_no_quiet_hours_config():
    assert not in_quiet_hours(datetime(2026, 7, 3, 3, 0), {})


def test_interval_in_configured_range():
    h = RetailerHealth()
    for _ in range(50):
        assert 120 <= check_interval(product("bestbuy"), CONFIG, h) <= 300
        assert 600 <= check_interval(product("pokemoncenter"), CONFIG, h) <= 900


def test_backoff_doubles_and_caps():
    h = RetailerHealth()
    assert h.record_blocked() is True   # first block -> warn once
    assert h.backoff == 2.0
    assert h.record_blocked() is False  # already warned
    for _ in range(10):
        h.record_blocked()
    assert h.backoff == RetailerHealth.MAX_BACKOFF
    interval = check_interval(product("bestbuy"), CONFIG, h)
    assert interval >= 120 * RetailerHealth.MAX_BACKOFF


def test_error_warns_only_at_fifth_consecutive():
    h = RetailerHealth()
    assert [h.record_error() for _ in range(6)] == [False, False, False, False, True, False]


def test_success_resets_health():
    h = RetailerHealth()
    h.record_blocked()
    for _ in range(5):
        h.record_error()
    h.record_success()
    assert h.backoff == 1.0
    assert h.consecutive_errors == 0
    assert h.record_blocked() is True  # warning re-armed after recovery


def test_check_interval_bad_shape_falls_back():
    h = RetailerHealth()
    bad = {"check_interval_seconds": 200}
    assert 120 <= check_interval(product("bestbuy"), bad, h) <= 300
    bad2 = {"check_interval_seconds": ["x", "y"]}
    assert 120 <= check_interval(product("bestbuy"), bad2, h) <= 300
    bad3 = {"check_interval_seconds": [100, 200, 300]}
    for _ in range(50):
        assert 120 <= check_interval(product("bestbuy"), bad3, h) <= 300


def test_quiet_hours_malformed_fails_open():
    from monitor import in_quiet_hours
    assert not in_quiet_hours(datetime(2026, 7, 3, 3, 0), {"quiet_hours": {"start": "25:99", "end": "07:00"}})
    assert not in_quiet_hours(datetime(2026, 7, 3, 3, 0), {"quiet_hours": {"start": "01:00"}})


def test_safe_reload_keeps_previous_on_error():
    from monitor import safe_reload

    def boom():
        raise json.JSONDecodeError("bad", "doc", 0)

    previous = ["sentinel"]
    assert safe_reload(boom, previous, "watchlist.json") is previous

    def ok():
        return ["fresh"]

    assert safe_reload(ok, previous, "watchlist.json") == ["fresh"]


def test_process_product_survives_state_save_failure(monkeypatch):
    import asyncio
    from collections import defaultdict

    import monitor
    from adapters import ADAPTERS
    from adapters.base import Status, StockResult

    class FakeAdapter:
        async def check(self, client, prod):
            return StockResult(status=Status.IN_STOCK, price=Decimal("1"))

    class FakeNotifier:
        def __init__(self):
            self.sent = []

        async def send(self, embed):
            self.sent.append(embed)

    def boom(states):
        raise OSError("disk full")

    monkeypatch.setitem(ADAPTERS, "faketailer", FakeAdapter())
    monkeypatch.setattr(monitor, "save_state", boom)

    notifier = FakeNotifier()
    states = {}
    health = defaultdict(RetailerHealth)
    prod = product("faketailer")
    # Must not raise, state must still be updated in memory, and the
    # restock alert must still be sent even though persistence failed.
    asyncio.run(monitor.process_product(None, notifier, prod, states, health))
    assert states[prod.key].price_ok is True
    assert any("RESTOCK" in embed.get("title", "") for embed in notifier.sent)


OVERRIDE_CONFIG = {
    "check_interval_seconds": [120, 300],
    "pokemoncenter_interval_seconds": [600, 900],
    "interval_overrides": {
        "pokemoncenter": [600, 900],
        "walmart": [600, 900],
        "ebgames": [600, 900],
    },
}


def test_interval_overrides_respected():
    h = RetailerHealth()
    for _ in range(50):
        assert 600 <= check_interval(product("walmart"), OVERRIDE_CONFIG, h) <= 900
        assert 600 <= check_interval(product("ebgames"), OVERRIDE_CONFIG, h) <= 900
        assert 600 <= check_interval(product("pokemoncenter"), OVERRIDE_CONFIG, h) <= 900
        # retailer with no override still uses the base config tier
        assert 120 <= check_interval(product("bestbuy"), OVERRIDE_CONFIG, h) <= 300


def test_interval_override_malformed_falls_back_to_next_tier():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "pokemoncenter_interval_seconds": [600, 900],
        "interval_overrides": {"walmart": 999},  # single number, not a 2-element range
    }
    for _ in range(50):
        # malformed override -> falls through to check_interval_seconds tier
        assert 120 <= check_interval(product("walmart"), config, h) <= 300


def test_interval_override_malformed_for_pokemoncenter_falls_back_to_legacy_key():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "pokemoncenter_interval_seconds": [600, 900],
        "interval_overrides": {"pokemoncenter": [100]},  # bad shape
    }
    for _ in range(50):
        assert 600 <= check_interval(product("pokemoncenter"), config, h) <= 900


def test_legacy_pokemoncenter_behavior_preserved_without_override():
    # No interval_overrides key at all: legacy behavior must be unchanged.
    h = RetailerHealth()
    for _ in range(50):
        assert 600 <= check_interval(product("pokemoncenter"), CONFIG, h) <= 900
        assert 120 <= check_interval(product("bestbuy"), CONFIG, h) <= 300


def test_interval_overrides_ignored_for_unlisted_retailer():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "interval_overrides": {"walmart": [600, 900]},
    }
    for _ in range(50):
        assert 120 <= check_interval(product("bestbuy"), config, h) <= 300
