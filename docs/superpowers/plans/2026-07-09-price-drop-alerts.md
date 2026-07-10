# Price-Drop Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert on Discord when an in-stock product hits a new all-time-low, buyable (≤ max) price that is meaningfully below the prior low — complementing the existing restock alert.

**Architecture:** A pure `should_alert_price_drop()` in `state.py`, a teal `build_price_drop_embed()` in `notifier.py`, and wiring in `process_product` that fires it as an `elif` after the restock alert, reusing the dashboard's persisted `lowest_price`. Thresholds come from config, parsed defensively.

**Tech Stack:** Python 3.9 (`from __future__ import annotations` in every module), pytest, asyncio, Decimal.

---

## File Structure

- `state.py` — add `should_alert_price_drop()` (pure).
- `notifier.py` — add `COLOR_PRICE_DROP` + `build_price_drop_embed()`.
- `monitor.py` — add `_config_float()` + `_to_decimal()` helpers and default constants; wire the drop alert into `process_product`; pass thresholds from `run()`.
- `tests/test_state.py`, `tests/test_notifier.py`, `tests/test_monitor_helpers.py` — tests.

---

## Task 1: `should_alert_price_drop` (pure, in `state.py`)

**Files:**
- Modify: `state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state.py` (it already imports `Product, Status, StockResult` from `adapters.base` and `Decimal`; add `should_alert_price_drop` to the `from state import ...` line):

```python
DROP_PROD = Product(name="Bundle", retailer="walmart", url="https://x",
                    max_price=Decimal("60"), packs=6)


def _drop_in_stock(price):
    return StockResult(status=Status.IN_STOCK,
                       price=Decimal(price) if price is not None else None)


def test_drop_new_buyable_low_above_threshold():
    # prior 49.98 -> 42.48: drop 7.50 (>$1, ~15% >2%), buyable (<=60)
    assert should_alert_price_drop(Decimal("49.98"), _drop_in_stock("42.48"), DROP_PROD, 0.02, 1.0) is True


def test_drop_not_a_new_low():
    assert should_alert_price_drop(Decimal("42.48"), _drop_in_stock("45.00"), DROP_PROD, 0.02, 1.0) is False


def test_drop_equal_to_prior_low():
    assert should_alert_price_drop(Decimal("42.48"), _drop_in_stock("42.48"), DROP_PROD, 0.02, 1.0) is False


def test_drop_over_max_not_buyable():
    # 65 < prior 80 (a new low) but 65 > max 60 -> not buyable
    assert should_alert_price_drop(Decimal("80"), _drop_in_stock("65"), DROP_PROD, 0.02, 1.0) is False


def test_drop_out_of_stock():
    oos = StockResult(status=Status.OUT_OF_STOCK, price=Decimal("42.48"))
    assert should_alert_price_drop(Decimal("49.98"), oos, DROP_PROD, 0.02, 1.0) is False


def test_drop_no_prior_low():
    assert should_alert_price_drop(None, _drop_in_stock("42.48"), DROP_PROD, 0.02, 1.0) is False


def test_drop_below_min_abs():
    # 43.00 -> 42.50: drop $0.50 (< $1 floor)
    assert should_alert_price_drop(Decimal("43.00"), _drop_in_stock("42.50"), DROP_PROD, 0.02, 1.0) is False


def test_drop_below_min_pct():
    # 1000 -> 999: drop $1 (>= floor) but 0.1% (< 2%)
    big = Product(name="Box", retailer="walmart", url="https://y", max_price=Decimal("2000"), packs=36)
    assert should_alert_price_drop(Decimal("1000"), _drop_in_stock("999"), big, 0.02, 1.0) is False


def test_drop_no_price():
    assert should_alert_price_drop(Decimal("49.98"), _drop_in_stock(None), DROP_PROD, 0.02, 1.0) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_state.py -q -k drop`
Expected: FAIL — `ImportError` (`should_alert_price_drop` doesn't exist).

- [ ] **Step 3: Implement in `state.py`**

Add `from decimal import Decimal` to the imports. Then add this function (e.g. just after `decide`):

```python
def should_alert_price_drop(prev_lowest, result, product, min_pct, min_abs) -> bool:
    """True iff `result` is an in-stock, buyable (<= max_price) new all-time low
    that is at least `min_abs` AND `min_pct` below `prev_lowest`. `prev_lowest`
    is the previously recorded lowest price (Decimal) or None (no prior low ->
    never a drop; that first buyable sighting is handled by the restock alert)."""
    if result.status is not Status.IN_STOCK or result.price is None:
        return False
    if result.price > product.max_price:            # not buyable
        return False
    if prev_lowest is None or result.price >= prev_lowest:   # no prior low / not a new low
        return False
    drop = prev_lowest - result.price
    return drop >= Decimal(str(min_abs)) and (drop / prev_lowest) >= Decimal(str(min_pct))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add state.py tests/test_state.py
git commit -m "feat: should_alert_price_drop (new buyable all-time low)"
```

---

## Task 2: `build_price_drop_embed` (in `notifier.py`)

**Files:**
- Modify: `notifier.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_notifier.py` (it already imports `Product, Status, StockResult`, `Decimal`, and defines `PRODUCT`; add `build_price_drop_embed, COLOR_PRICE_DROP` to the `from notifier import ...` line):

```python
def test_price_drop_embed():
    result = StockResult(status=Status.IN_STOCK, price=Decimal("42.48"))
    embed = build_price_drop_embed(PRODUCT, result, Decimal("49.98"))
    assert embed["color"] == COLOR_PRICE_DROP
    assert "Price drop" in embed["title"]
    assert embed["url"] == PRODUCT.url
    assert "42.48" in embed["description"]   # new price
    assert "49.98" in embed["description"]   # previous low
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -q -k price_drop`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement in `notifier.py`**

Add the color constant next to `COLOR_RESTOCK`:

```python
COLOR_PRICE_DROP = 0x1ABC9C   # teal
```

Add the builder (e.g. just after `build_restock_embed`):

```python
def build_price_drop_embed(product: Product, result: StockResult, prev_lowest) -> dict:
    pct = (prev_lowest - result.price) / prev_lowest * 100
    per_pack = result.price / product.packs
    return {
        "title": f"📉 Price drop: {product.name}",
        "url": product.url,
        "color": COLOR_PRICE_DROP,
        "description": (
            f"**${result.price:.2f} CAD** (was ${prev_lowest:.2f}, −{pct:.0f}%) "
            f"· ${per_pack:.2f}/pack at **{product.retailer}**\n"
            f"[Buy now]({product.url})"
        ),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_notifier.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier.py tests/test_notifier.py
git commit -m "feat: teal price-drop Discord embed"
```

---

## Task 3: `_config_float` defensive threshold parser (in `monitor.py`)

**Files:**
- Modify: `monitor.py`
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_monitor_helpers.py`:

```python
def test_config_float_parsing():
    from monitor import _config_float
    assert _config_float({"k": 0.05}, "k", 0.02) == 0.05
    assert _config_float({}, "k", 0.02) == 0.02
    assert _config_float({"k": "oops"}, "k", 0.02) == 0.02
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_config_float_parsing -q`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement in `monitor.py`**

Add these near `_product_health_threshold` (they define the drop defaults and a reusable defensive float parser):

```python
DEFAULT_PRICE_DROP_MIN_PCT = 0.02
DEFAULT_PRICE_DROP_MIN_ABS = 1.0


def _config_float(config: dict, key: str, default: float) -> float:
    """Read a float config value, falling back to `default` (with a warning) on a
    missing or non-numeric value — a bad config edit must not crash the daemon."""
    raw = config.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning("bad %s in config (%r); using %s", key, raw, default)
        return default
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_config_float_parsing -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: _config_float defensive parser + price-drop defaults"
```

---

## Task 4: Wire the price-drop alert into `process_product` + `run()`

**Files:**
- Modify: `monitor.py` (imports, `_to_decimal`, `process_product`, `run()` call site)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_monitor_helpers.py`:

```python
def test_process_product_fires_price_drop(monkeypatch):
    import monitor
    from adapters import ADAPTERS
    from dashboard import DashboardRecord

    class PriceAdapter:
        async def check(self, c, p):
            return StockResult(status=Status.IN_STOCK, price=Decimal("42.48"), title="Bundle")

    monkeypatch.setitem(ADAPTERS, "dropt", PriceAdapter())
    monkeypatch.setattr(monitor, "save_state", lambda s: None)
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    class FakeNotifier:
        def __init__(self): self.sent = []
        async def send(self, e): self.sent.append(e)

    notifier = FakeNotifier()
    prod = Product(name="Bundle", retailer="dropt", url="https://x",
                   max_price=Decimal("60"), packs=6)
    # already buyable (so restock won't fire), prior low higher than the new price
    states = {prod.key: ProductState(status="in_stock", price_ok=True)}
    records = {prod.key: DashboardRecord(last_price="49.98", last_status="in_stock",
                                         lowest_price="49.98")}
    health = defaultdict(RetailerHealth)
    asyncio.run(monitor.process_product(None, notifier, prod, states, records, health,
                                        None, 5, 0.02, 1.0))
    titles = " ".join(e.get("title", "") for e in notifier.sent)
    assert "Price drop" in titles


def test_process_product_restock_suppresses_price_drop(monkeypatch):
    import monitor
    from adapters import ADAPTERS
    from dashboard import DashboardRecord

    class PriceAdapter:
        async def check(self, c, p):
            return StockResult(status=Status.IN_STOCK, price=Decimal("42.48"), title="Bundle")

    monkeypatch.setitem(ADAPTERS, "dropt2", PriceAdapter())
    monkeypatch.setattr(monitor, "save_state", lambda s: None)
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    class FakeNotifier:
        def __init__(self): self.sent = []
        async def send(self, e): self.sent.append(e)

    notifier = FakeNotifier()
    prod = Product(name="Bundle", retailer="dropt2", url="https://z",
                   max_price=Decimal("60"), packs=6)
    # was OUT of stock -> becoming buyable at a new low is a RESTOCK, not a drop
    states = {prod.key: ProductState(status="out_of_stock", price_ok=False)}
    records = {prod.key: DashboardRecord(last_price="49.98", last_status="out_of_stock",
                                         lowest_price="49.98")}
    health = defaultdict(RetailerHealth)
    asyncio.run(monitor.process_product(None, notifier, prod, states, records, health,
                                        None, 5, 0.02, 1.0))
    titles = " ".join(e.get("title", "") for e in notifier.sent)
    assert "RESTOCK" in titles
    assert "Price drop" not in titles
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q -k "price_drop or suppresses"`
Expected: FAIL — `process_product()` takes 8 args, not 10 (`TypeError`).

- [ ] **Step 3: Add imports + `_to_decimal` in `monitor.py`**

Update the `state` and `notifier` imports to include the new names:

```python
from notifier import (
    Notifier,
    build_heartbeat_embed,
    build_price_drop_embed,
    build_restock_embed,
    build_system_embed,
)
from state import ProductState, decide, load_state, save_state, should_alert_price_drop
```

Add a small converter near the other helpers (monitor.py already imports `Decimal`):

```python
def _to_decimal(s):
    """Parse a stringified Decimal to Decimal, or None if missing/malformed."""
    if s is None:
        return None
    try:
        return Decimal(s)
    except (ArithmeticError, TypeError):
        return None
```

- [ ] **Step 4: Extend `process_product` (signature + capture prior low + elif)**

Change the signature to add the two threshold params (defaults keep existing callers working):

```python
async def process_product(client, notifier, product, states, records, health,
                          product_health=None, health_threshold=DEFAULT_PRODUCT_HEALTH_THRESHOLD,
                          price_drop_min_pct=DEFAULT_PRICE_DROP_MIN_PCT,
                          price_drop_min_abs=DEFAULT_PRICE_DROP_MIN_ABS) -> bool:
```

Find the dashboard-update line:

```python
    new_rec, changed = update_record(records.get(product.key, DashboardRecord()), result, now)
```

Replace it with a version that captures the prior low first:

```python
    prev_rec = records.get(product.key, DashboardRecord())
    prior_lowest = _to_decimal(prev_rec.lowest_price)
    new_rec, changed = update_record(prev_rec, result, now)
```

Find the alert block at the end:

```python
    if decision.alert == "restock":
        await notifier.send(build_restock_embed(product, result))
    return changed
```

Replace with (drop as an `elif` so a restock at a new low fires only once):

```python
    if decision.alert == "restock":
        await notifier.send(build_restock_embed(product, result))
    elif should_alert_price_drop(prior_lowest, result, product, price_drop_min_pct, price_drop_min_abs):
        await notifier.send(build_price_drop_embed(product, result, prior_lowest))
    return changed
```

- [ ] **Step 5: Pass thresholds from `run()`**

At the `process_product(...)` call inside `run()`'s loop, add the two config-sourced thresholds:

```python
                        changed = await asyncio.wait_for(
                            process_product(client, notifier, product, states, records, health,
                                            product_health, _product_health_threshold(config),
                                            _config_float(config, "price_drop_min_pct", DEFAULT_PRICE_DROP_MIN_PCT),
                                            _config_float(config, "price_drop_min_abs", DEFAULT_PRICE_DROP_MIN_ABS)),
                            timeout=180,
                        )
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (existing 6/8-arg `process_product` tests still pass via defaults).

- [ ] **Step 7: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: alert on a new buyable all-time-low price"
```

---

## Deployment (post-merge, on `pmonitor`)

Code change → restart required:

```bash
bash /Users/Shared/pmonitor-deploy.sh
```

Optional overrides in `pmonitor`'s `config.json`: `price_drop_min_pct` (default 0.02) / `price_drop_min_abs` (default 1.0).

---

## Self-Review

- **Spec coverage:** trigger `should_alert_price_drop` → Task 1; embed → Task 2; defensive thresholds → Task 3; wiring (capture prior low, `elif`, run() thresholds) → Task 4. Data flow (reuse `lowest_price`, restock-suppresses-drop) covered by Task 4 tests. All spec sections covered.
- **Type consistency:** `should_alert_price_drop(prev_lowest, result, product, min_pct, min_abs) -> bool`, `build_price_drop_embed(product, result, prev_lowest) -> dict`, `_config_float(config, key, default) -> float`, `_to_decimal(s) -> Decimal|None`, `process_product(..., price_drop_min_pct=DEFAULT_PRICE_DROP_MIN_PCT, price_drop_min_abs=DEFAULT_PRICE_DROP_MIN_ABS)` used consistently across tasks.
- **Placeholders:** none — every step has concrete code + commands.
