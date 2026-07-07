# Wider, Faster Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the restock-miss gaps by adding a drop-window turbo mode, a block-recovery notice, and Walmart coverage, plus config tuning — so we catch drops like Pitch Black (PC) and Prismatic (Walmart) that slipped past.

**Architecture:** Turbo pacing is a pure addition to `check_interval()` driven by a `drop_windows` list in config; the recovery notice is a small `RetailerHealth`/`process_product` change; Walmart coverage is watchlist data using the existing adapter. No new modules.

**Tech Stack:** Python 3.9 (`from __future__ import annotations` in every module), pytest, asyncio, httpx, Playwright (headed real Chrome for browser retailers).

**Prerequisite:** PR #11 (browser/socket-lifecycle hardening) must be merged first. Create the implementation branch off the updated `main` (or rebase `feature/wider-faster-net` onto `main`) before starting — this feature increases check frequency and PR #11 is the guardrail.

---

## File Structure

- `monitor.py` — add `_match_drop_window()`, `_active_turbo_interval()`, a `TURBO_INTERVAL_FLOOR` constant, and a turbo branch + `now` param in `check_interval()`; make `RetailerHealth.record_success()` return a bool; send a recovery embed in `process_product()`; pass `now` at the `check_interval` call site.
- `tests/test_monitor_helpers.py` — add turbo-interval tests and the recovery-notice test.
- `adapters/walmart.py`, `adapters/pokemoncenter.py` — audit only; confirm a challenge page raises `Blocked` (fix if not).
- `watchlist.json` — add ~6–8 Walmart entries.
- `docs/superpowers/plans/...` (this file), spec already committed.

---

## Task 1: Drop-window turbo mode in `check_interval`

**Files:**
- Modify: `monitor.py` (add helpers + constant near `check_interval`, ~line 115–151; add `now` param and turbo branch)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_monitor_helpers.py`:

```python
def product_set(retailer, set_name):
    return Product(
        name="x", retailer=retailer, url="https://x",
        max_price=Decimal("1"), set_name=set_name,
    )


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
    assert 120 <= val <= 300  # falls back to normal bestbuy override


def test_turbo_ignored_when_set_does_not_match():
    h = RetailerHealth()
    val = check_interval(product_set("bestbuy", "151"), DROP_CONFIG, h, INSIDE_WINDOW)
    assert 120 <= val <= 300


def test_turbo_ignores_backoff():
    h = RetailerHealth()
    h.backoff = 16.0  # heavily backed off
    val = check_interval(product_set("bestbuy", "Pitch Black"), DROP_CONFIG, h, INSIDE_WINDOW)
    assert 60 <= val <= 120  # NOT multiplied by backoff


def test_turbo_respects_floor():
    h = RetailerHealth()
    config = {
        "drop_windows": [{
            "label": "x", "start": "2026-07-17T08:00:00", "end": "2026-07-17T14:00:00",
            "match": {"set": "Pitch Black"}, "interval": [5, 5],
        }]
    }
    val = check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW)
    assert val == 30.0  # floored


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
    # matches only when BOTH set and retailer match
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
             "match": {}, "interval": [90, 90]},                       # empty match
            {"label": "bad-date", "start": "not-a-date", "end": "x",
             "match": {"set": "Pitch Black"}, "interval": [90, 90]},   # bad datetime
            "not-a-dict",                                              # wrong type
        ],
    }
    val = check_interval(product_set("bestbuy", "Pitch Black"), config, h, INSIDE_WINDOW)
    assert 120 <= val <= 300  # all skipped -> normal tier


def test_check_interval_now_defaults_to_wall_clock():
    # Omitting now must not raise and must return a normal interval when no
    # drop_windows are configured.
    h = RetailerHealth()
    assert 120 <= check_interval(product("bestbuy"), CONFIG, h) <= 300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q -k turbo`
Expected: FAIL — `check_interval()` takes 3 positional args (no `now`), `TypeError`.

- [ ] **Step 3: Implement the turbo logic**

In `monitor.py`, add above `check_interval` (after `_parse_interval_range`, ~line 125):

```python
TURBO_INTERVAL_FLOOR = 30.0  # never poll faster than this, even in a drop window


def _match_drop_window(window, product: Product, now: datetime) -> tuple[float, float] | None:
    """Return (low, high) turbo seconds if `product` matches `window` and `now`
    falls inside it; else None. Malformed windows are logged and skipped so a
    bad config entry can never crash the loop."""
    if not isinstance(window, dict):
        log.warning("bad drop_window (%r); skipping", window)
        return None
    try:
        start = datetime.fromisoformat(window["start"])
        end = datetime.fromisoformat(window["end"])
        match = window["match"]
        interval = window["interval"]
    except (KeyError, TypeError) as exc:
        log.warning("bad drop_window (%r); skipping: %s", window, exc)
        return None
    except ValueError as exc:  # unparseable datetime string
        log.warning("bad drop_window date (%r); skipping: %s", window, exc)
        return None
    if not isinstance(match, dict) or not match:
        log.warning("drop_window match must be a non-empty object (%r); skipping", window)
        return None
    if not (start <= now < end):
        return None
    if "set" in match and product.set_name != match["set"]:
        return None
    if "retailer" in match and product.retailer != match["retailer"]:
        return None
    return _parse_interval_range(interval, "drop_window.interval")


def _active_turbo_interval(product: Product, config: dict, now: datetime) -> tuple[float, float] | None:
    """Shortest matching drop-window turbo interval for `product` at `now`, or
    None if no window is active/matching."""
    windows = config.get("drop_windows")
    if not isinstance(windows, list):
        return None
    best = None
    for window in windows:
        rng = _match_drop_window(window, product, now)
        if rng is None:
            continue
        if best is None or rng[1] < best[1]:  # shortest by upper bound
            best = rng
    return best
```

Then change the `check_interval` signature and add the turbo branch at the top of its body:

```python
def check_interval(product: Product, config: dict, health: RetailerHealth,
                   now: datetime | None = None) -> float:
    if now is None:
        now = datetime.now()

    # Drop-window turbo overrides everything and ignores backoff (we want to keep
    # trying through intermittent blocks during a known drop), but never drops
    # below the floor.
    turbo = _active_turbo_interval(product, config, now)
    if turbo is not None:
        low, high = turbo
        return max(random.uniform(low, high), TURBO_INTERVAL_FLOOR)

    low_high = None
    # ... existing override / pokemoncenter / check_interval_seconds / default tiers unchanged ...
```

(Leave the rest of the function body exactly as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q`
Expected: PASS (all existing + new turbo tests).

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: drop-window turbo mode for check_interval"
```

---

## Task 2: Pass `now` at the `check_interval` call site

**Files:**
- Modify: `monitor.py` (the `next_check[...]` assignment in `run()`, ~line 253)

- [ ] **Step 1: Update the call site**

Find:

```python
                        next_check[product.key] = datetime.now() + timedelta(
                            seconds=check_interval(product, config, health[product.retailer])
                        )
```

Replace the inner call with one that passes the loop's `now`:

```python
                        next_check[product.key] = datetime.now() + timedelta(
                            seconds=check_interval(product, config, health[product.retailer], now)
                        )
```

(`now` is already in scope in the loop. If PR #11's merge shifted the line number, locate the single `check_interval(` call in `run()` and add `, now`.)

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no behavior change; `now` was optional).

- [ ] **Step 3: Commit**

```bash
git add monitor.py
git commit -m "feat: feed loop clock into check_interval for drop windows"
```

---

## Task 3: Block-recovery notice

**Files:**
- Modify: `monitor.py` (`RetailerHealth.record_success`, ~line 64; `process_product`, ~line 173)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_monitor_helpers.py`:

```python
def test_record_success_reports_recovery_once():
    h = RetailerHealth()
    assert h.record_success() is False        # was never blocked
    h.record_blocked()                        # now blocked
    assert h.record_success() is True         # first success after block -> recovered
    assert h.record_success() is False        # subsequent successes are quiet
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_record_success_reports_recovery_once -q`
Expected: FAIL — `record_success()` returns `None`, not `False`.

- [ ] **Step 3: Make `record_success` return whether it ended a block episode**

Replace `RetailerHealth.record_success`:

```python
    def record_success(self) -> bool:
        """Returns True if this success ends a blocked episode (caller may post a
        recovery notice)."""
        was_blocked = self.warned_blocked
        self.backoff = 1.0
        self.consecutive_errors = 0
        self.warned_blocked = False
        self.warned_errors = False
        return was_blocked
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_record_success_reports_recovery_once -q`
Expected: PASS

- [ ] **Step 5: Send the recovery embed in `process_product`**

Find (in `process_product`, just after the adapter call succeeds, ~line 173):

```python
    h.record_success()
    now = datetime.now()
```

Replace with:

```python
    if h.record_success():
        await notifier.send(
            build_system_embed(f"**{product.retailer}** checks recovered — visibility restored.")
        )
    now = datetime.now()
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: notify when a blocked retailer recovers"
```

---

## Task 4: Audit that browser adapters raise `Blocked`

**Files:**
- Inspect: `adapters/walmart.py`, `adapters/pokemoncenter.py`, `adapters/base.py` (the `Blocked` exception + `raise_if_blocked`)

- [ ] **Step 1: Read the two browser adapters' challenge handling**

Run: `.venv/bin/python -c "import inspect, adapters.walmart as w, adapters.pokemoncenter as p; print(inspect.getsource(w)); print('====='); print(inspect.getsource(p))"`

Confirm: when the fetched page looks like a challenge/block (Walmart: `looks_blocked`; PC: `is_challenge_page`), the adapter **raises `Blocked`** (not returns a `StockResult` with `Status.UNKNOWN`/`OUT_OF_STOCK`). The existing `process_product` block-alert only fires on a raised `Blocked`, so a silent return would leave us blind with no alert.

- [ ] **Step 2: If either adapter returns silently on a challenge, fix it**

Only if the audit finds a silent path: make the challenge branch `raise Blocked(<retailer>, "<reason>")` consistent with the other adapter. Add/adjust a unit test in that adapter's test file (`tests/test_walmart.py` / `tests/test_pokemoncenter.py`) asserting a challenge HTML input raises `Blocked`. If both already raise, note "no change needed" and skip to Step 3.

- [ ] **Step 3: Run the suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 4: Commit (only if a fix was made)**

```bash
git add adapters/ tests/
git commit -m "fix: raise Blocked on <retailer> challenge so block alerts fire"
```

---

## Task 5: Walmart coverage

**Files:**
- Modify: `watchlist.json`

Target: add ~6–8 Walmart entries — the booster box and ETB (and bundle where it exists) for the highest-demand in-print sets Walmart carries. Prioritize: **Pitch Black**, **Chaos Rising**, **Perfect Order**, **Mega Evolution (base)** (add others only if throughput allows). Each entry uses the existing `walmart` adapter — the work is finding the real URL and verifying it.

- [ ] **Step 1: Source each product's walmart.ca URL**

For each target product, find its canonical walmart.ca product page (search `walmart.ca` for e.g. "Pokemon Mega Evolution Pitch Black Booster Box"). Record the `/en/ip/.../<id>` URL. Do NOT guess URLs — each must resolve to the correct product.

- [ ] **Step 2: Add entries to `watchlist.json`**

For each, append an object to `products` matching the existing schema (set the `max_price` per the pricing rule: ~$10 CAD/pack, so booster box packs=36 → ~$160; ETB → per its pack count):

```json
    {
      "name": "Pitch Black Booster Box (36 packs)",
      "retailer": "walmart",
      "url": "https://www.walmart.ca/en/ip/.../<id>",
      "max_price": 160.0,
      "set": "Pitch Black",
      "packs": 36
    }
```

- [ ] **Step 3: Validate JSON and live-verify each URL**

Run: `.venv/bin/python -c "import json; json.load(open('watchlist.json')); print('valid')"`

Then live-verify each new Walmart URL returns the correct product page unblocked (throwaway profile so it doesn't collide with the running monitor):

```bash
.venv/bin/python -c "
import asyncio, re
from adapters.browser import fetch_page_html
url='<the-new-url>'
html=asyncio.run(fetch_page_html(url, profile='walmart-verify', settle_ms=5000, headless=False, channel='chrome'))
low=html.lower()
print('blocked:', any(m in low for m in ('robot or human','px-captcha','access denied','pardon our interruption')))
t=re.search(r'<title>(.*?)</title>', html, re.S|re.I); print('title:', t and t.group(1).strip()[:80])
"
```
Expected: `blocked: False` and a title matching the product.

- [ ] **Step 4: Commit**

```bash
git add watchlist.json
git commit -m "feat: add Walmart coverage for high-demand in-print sets"
```

---

## Deployment (post-merge, on `pmonitor` — NOT code)

After the PR merges and `pmonitor` pulls, apply the config tuning to `pmonitor`'s `config.json` (it's gitignored/per-deployment and hot-reloads — no restart needed):

- Add/adjust `interval_overrides`:
  ```json
  "interval_overrides": {
    "bestbuy":       [60, 90],
    "walmart":       [900, 1200],
    "ebgames":       [900, 1200],
    "pokemoncenter": [900, 1200]
  }
  ```
- Seed the Pitch Black drop window:
  ```json
  "drop_windows": [
    {
      "label": "Pitch Black launch",
      "start": "2026-07-17T08:00:00",
      "end":   "2026-07-17T14:00:00",
      "match": { "set": "Pitch Black" },
      "interval": [60, 120]
    }
  ]
  ```

Verify in `pmonitor`: `tail -f ~/pokemon-monitor/logs/monitor.log` — Best Buy lines should now recur ~every 60–90s.

---

## Self-Review

- **Spec coverage:** config tuning → Deployment section; Walmart coverage → Task 5; drop-window turbo → Tasks 1–2; block-alert polish (recovered notice + adapter audit) → Tasks 3–4. All spec sections covered.
- **Type consistency:** `check_interval(product, config, health, now=None)` used consistently; `_active_turbo_interval`/`_match_drop_window` return `tuple[float,float] | None`; `record_success` returns `bool`; matching uses `product.set_name` (the field `load_watchlist` populates from `set`).
- **Placeholders:** Walmart URLs are intentionally sourced at execution (Step 1 of Task 5) — every other step has concrete code/commands.
