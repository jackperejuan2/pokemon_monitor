import asyncio
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from adapters.base import Product
from monitor import RetailerHealth, _skip_for_quiet_hours, check_interval, in_quiet_hours

CONFIG = {
    "check_interval_seconds": [120, 300],
    "pokemoncenter_interval_seconds": [600, 900],
    "quiet_hours": {"start": "01:30", "end": "07:00"},
}


def product(retailer):
    return Product(name="x", retailer=retailer, url="https://x", max_price=Decimal("1"))


def product_set(retailer, set_name):
    return Product(
        name="x", retailer=retailer, url="https://x",
        max_price=Decimal("1"), set_name=set_name,
    )


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


def test_record_success_reports_recovery_once():
    h = RetailerHealth()
    assert h.record_success() is False        # was never blocked
    h.record_blocked()                        # now blocked
    assert h.record_success() is True         # first success after block -> recovered
    assert h.record_success() is False        # subsequent successes are quiet


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
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    notifier = FakeNotifier()
    states = {}
    records = {}
    health = defaultdict(RetailerHealth)
    prod = product("faketailer")
    # Must not raise, state must still be updated in memory, and the
    # restock alert must still be sent even though persistence failed.
    asyncio.run(monitor.process_product(None, notifier, prod, states, records, health))
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


def test_process_product_updates_records_and_reports_change(monkeypatch):
    import asyncio
    from decimal import Decimal
    import monitor
    from adapters import ADAPTERS
    from adapters.base import Product, Status, StockResult

    product = Product(name="X", retailer="faketest", url="https://x", max_price=Decimal("90"),
                      packs=9, set_name="151")

    class FakeAdapter:
        async def check(self, client, prod):
            return StockResult(status=Status.IN_STOCK, price=Decimal("50.00"), title="X",
                               url=prod.url)

    monkeypatch.setitem(ADAPTERS, "faketest", FakeAdapter())
    monkeypatch.setattr(monitor, "save_state", lambda s: None)
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    class NullNotifier:
        async def send(self, embed):
            pass

    states, records, health = {}, {}, __import__("collections").defaultdict(monitor.RetailerHealth)
    changed = asyncio.run(
        monitor.process_product(None, NullNotifier(), product, states, records, health)
    )
    assert changed is True
    assert records[product.key].last_price == "50.00"


def test_load_watchlist_reads_packs_and_set(tmp_path, monkeypatch):
    import json
    import monitor
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"products": [
        {"name": "A", "retailer": "bestbuy", "url": "https://a", "max_price": 90, "sku": "1",
         "packs": 9, "set": "151"},
        {"name": "B", "retailer": "walmart", "url": "https://b", "max_price": 60},
    ]}))
    monkeypatch.setattr(monitor, "WATCHLIST_PATH", wl)
    products = monitor.load_watchlist()
    assert products[0].packs == 9 and products[0].set_name == "151"
    assert products[1].packs == 1 and products[1].set_name == "Other"


DROP_CONFIG = {
    "check_interval_seconds": [120, 300],
    "interval_overrides": {"bestbuy": [120, 300]},
    "drop_windows": [
        {
            "label": "Pitch Black launch",
            "start": "2026-07-17T08:00:00",
            "end": "2026-07-17T14:00:00",
            "match": {"set": "Pitch Black"},
            "interval": [60, 120],
        }
    ],
}
INSIDE_WINDOW = datetime(2026, 7, 17, 10, 0, 0)
OUTSIDE_WINDOW = datetime(2026, 7, 17, 15, 0, 0)


def test_turbo_active_when_inside_window_and_set_matches():
    h = RetailerHealth()
    for _ in range(50):
        val = check_interval(product_set("bestbuy", "Pitch Black"), DROP_CONFIG, h, INSIDE_WINDOW)
        assert 60 <= val <= 120


def test_turbo_ignored_outside_window():
    h = RetailerHealth()
    val = check_interval(product_set("bestbuy", "Pitch Black"), DROP_CONFIG, h, OUTSIDE_WINDOW)
    assert 120 <= val <= 300


def test_turbo_ignored_when_set_does_not_match():
    h = RetailerHealth()
    val = check_interval(product_set("bestbuy", "151"), DROP_CONFIG, h, INSIDE_WINDOW)
    assert 120 <= val <= 300


def test_turbo_ignores_backoff():
    h = RetailerHealth()
    h.backoff = 16.0
    val = check_interval(product_set("bestbuy", "Pitch Black"), DROP_CONFIG, h, INSIDE_WINDOW)
    assert 60 <= val <= 120


def test_turbo_respects_floor():
    h = RetailerHealth()
    config = {
        "drop_windows": [{
            "label": "x", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
            "match": {"set": "Pitch Black"}, "interval": [5, 5],
        }]
    }
    val = check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW)
    assert val == 30.0


def test_turbo_matches_on_retailer_key():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "drop_windows": [{
            "label": "wm", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
            "match": {"retailer": "walmart"}, "interval": [90, 90],
        }]
    }
    assert check_interval(product_set("walmart", "151"), config, h, INSIDE_WINDOW) == 90.0
    assert 120 <= check_interval(product_set("bestbuy", "151"), config, h, INSIDE_WINDOW) <= 300


def test_turbo_requires_all_match_keys():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "drop_windows": [{
            "label": "both", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
            "match": {"set": "Pitch Black", "retailer": "walmart"}, "interval": [90, 90],
        }]
    }
    assert check_interval(product_set("walmart", "Pitch Black"), config, h, INSIDE_WINDOW) == 90.0
    assert 120 <= check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW) <= 300


def test_turbo_shortest_window_wins():
    h = RetailerHealth()
    config = {
        "drop_windows": [
            {"label": "a", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
             "match": {"set": "Pitch Black"}, "interval": [200, 200]},
            {"label": "b", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
             "match": {"set": "Pitch Black"}, "interval": [90, 90]},
        ]
    }
    assert check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW) == 90.0


def test_turbo_malformed_window_skipped():
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "drop_windows": [
            {"label": "no-match", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
             "match": {}, "interval": [90, 90]},
            {"label": "bad-date", "start": "not-a-date", "end": "x",
             "match": {"set": "Pitch Black"}, "interval": [90, 90]},
            "not-a-dict",
        ],
    }
    val = check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW)
    assert 120 <= val <= 300


def test_turbo_unsupported_match_key_skipped():
    # A typo'd/unsupported match key must NOT match every product; the window
    # is malformed and we fall back to the normal tier.
    h = RetailerHealth()
    config = {
        "check_interval_seconds": [120, 300],
        "drop_windows": [{
            "label": "typo", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
            "match": {"st": "Pitch Black"}, "interval": [90, 90],
        }],
    }
    val = check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW)
    assert 120 <= val <= 300


def test_check_interval_now_defaults_to_wall_clock():
    h = RetailerHealth()
    assert 120 <= check_interval(product("bestbuy"), CONFIG, h) <= 300


def test_skip_for_quiet_hours_false_when_not_quiet():
    assert _skip_for_quiet_hours(product("bestbuy"), {}, INSIDE_WINDOW, False) is False


def test_skip_for_quiet_hours_true_when_quiet_and_no_window():
    assert _skip_for_quiet_hours(product("bestbuy"), {}, INSIDE_WINDOW, True) is True


def test_skip_for_quiet_hours_exempts_active_drop_window():
    # quiet, but the product matches an active drop window -> must NOT skip
    assert _skip_for_quiet_hours(
        product_set("bestbuy", "Pitch Black"), DROP_CONFIG, INSIDE_WINDOW, True
    ) is False


def test_recovery_notice_sent_after_block_then_success(monkeypatch):
    import monitor
    from adapters import ADAPTERS
    from adapters.base import Status, StockResult, Blocked

    class FlakyAdapter:
        def __init__(self):
            self.calls = 0
        async def check(self, client, prod):
            self.calls += 1
            if self.calls == 1:
                raise Blocked("blocked once")
            return StockResult(status=Status.OUT_OF_STOCK)

    class FakeNotifier:
        def __init__(self):
            self.sent = []
        async def send(self, embed):
            self.sent.append(embed)

    monkeypatch.setitem(ADAPTERS, "flaky", FlakyAdapter())
    monkeypatch.setattr(monitor, "save_state", lambda s: None)
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    notifier = FakeNotifier()
    states, records = {}, {}
    health = defaultdict(RetailerHealth)
    prod = product("flaky")
    asyncio.run(monitor.process_product(None, notifier, prod, states, records, health))  # blocked
    asyncio.run(monitor.process_product(None, notifier, prod, states, records, health))  # recovers
    blob = " ".join((e.get("title", "") + " " + e.get("description", "")) for e in notifier.sent)
    assert "recovered" in blob.lower()
