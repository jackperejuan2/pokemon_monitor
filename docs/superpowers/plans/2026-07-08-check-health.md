# Check Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make EB Games checks return real data and add its 15 SKUs, plus a product-level health guard that surfaces any SKU that stops returning usable data (so nothing silently renders wrong on the dashboard or fails to alert).

**Architecture:** `ebgames` becomes browser-first in `JsonLdAdapter`. A new in-memory `ProductHealth` (mirroring `RetailerHealth`) tracks per-product "no usable data" streaks and alerts via Discord. `--check-once` gains a usable-data summary.

**Tech Stack:** Python 3.9 (`from __future__ import annotations` in every module), pytest, asyncio, httpx, Playwright (headed real Chrome).

---

## File Structure

- `adapters/jsonld.py` — add `browser_first` flag to `JsonLdAdapter`.
- `adapters/__init__.py` — register `ebgames` as `JsonLdAdapter(browser_first=True)`.
- `monitor.py` — add `is_usable()` + `ProductHealth`; wire into `process_product`/`run()`; add the `--check-once` usable-data summary.
- `tests/test_jsonld.py` — browser-first adapter tests.
- `tests/test_monitor_helpers.py` — `is_usable`/`ProductHealth`/wiring/`--check-once` tests.
- `watchlist.json` — 15 EB Games SKUs.

---

## Task 1: `browser_first` flag on `JsonLdAdapter`

**Files:**
- Modify: `adapters/jsonld.py` (`JsonLdAdapter`)
- Modify: `adapters/__init__.py` (registry)
- Test: `tests/test_jsonld.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jsonld.py`:

```python
import asyncio
from decimal import Decimal

from adapters.jsonld import JsonLdAdapter
from adapters.base import Product, Status, StockResult


def _ebg_product():
    return Product(name="x", retailer="ebgames", url="https://x", max_price=Decimal("1"))


def test_browser_first_skips_httpx(monkeypatch):
    adapter = JsonLdAdapter(browser_first=True)

    async def fake_browser(product):
        return StockResult(status=Status.IN_STOCK, price=Decimal("9"), title="T", url=product.url)

    monkeypatch.setattr(adapter, "_check_via_browser", fake_browser)

    class Client:
        async def get(self, url):
            raise AssertionError("httpx must not be used when browser_first=True")

    result = asyncio.run(adapter.check(Client(), _ebg_product()))
    assert result.status is Status.IN_STOCK and result.title == "T"


def test_default_uses_httpx_first():
    adapter = JsonLdAdapter()  # browser_first defaults False
    calls = []

    class Resp:
        text = "<html></html>"
        url = "https://x"
        status_code = 200
        def raise_for_status(self):
            pass

    class Client:
        async def get(self, url):
            calls.append(url)
            return Resp()

    asyncio.run(adapter.check(Client(), _ebg_product()))
    assert calls == ["https://x"]  # httpx path was taken
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_jsonld.py -q -k browser_first`
Expected: FAIL — `JsonLdAdapter()` takes no `browser_first` kwarg (`TypeError`).

- [ ] **Step 3: Add the flag and branch**

In `adapters/jsonld.py`, add an `__init__` to `JsonLdAdapter` (just above its `check` method) and a browser-first branch at the top of `check`:

```python
class JsonLdAdapter:
    def __init__(self, browser_first: bool = False) -> None:
        self.browser_first = browser_first

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        if self.browser_first:
            # Some sites (EB Games) only expose their JSON-LD when JS-rendered;
            # httpx returns a product-node-less page that the marker fallback
            # misreads. Go straight to a real browser render for those.
            return await self._check_via_browser(product)
        try:
            response = await client.get(product.url)
            raise_if_blocked(response)
            response.raise_for_status()
        except Blocked:
            return await self._check_via_browser(product)
        return parse_stock_from_html(response.text, product.url)
```

(Leave `_check_via_browser` and the class docstring, if any, unchanged.)

- [ ] **Step 4: Register `ebgames` as browser-first**

In `adapters/__init__.py`, change the `ebgames` entry:

```python
    "ebgames": JsonLdAdapter(browser_first=True),
```

(Leave `toysrus`, `indigo`, `costco` as plain `JsonLdAdapter()`.)

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_jsonld.py -q`
Expected: PASS (new + existing).

- [ ] **Step 6: Commit**

```bash
git add adapters/jsonld.py adapters/__init__.py tests/test_jsonld.py
git commit -m "feat: browser-first JsonLdAdapter; force browser render for EB Games"
```

---

## Task 2: `is_usable` + `ProductHealth` (pure logic)

**Files:**
- Modify: `monitor.py` (add near `RetailerHealth`; ensure `Status` is imported)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_monitor_helpers.py` (add `from adapters.base import Status, StockResult` to its imports if not already present):

```python
from monitor import ProductHealth, is_usable  # add to existing monitor import line


def _res(status, title="Real Product"):
    return StockResult(status=status, title=title)


def test_is_usable_rules():
    assert is_usable(_res(Status.IN_STOCK)) is True
    assert is_usable(_res(Status.OUT_OF_STOCK)) is True
    assert is_usable(_res(Status.UNKNOWN)) is False           # unknown status
    assert is_usable(_res(Status.OUT_OF_STOCK, title="")) is False    # empty title
    assert is_usable(_res(Status.IN_STOCK, title="   ")) is False     # whitespace title


def test_product_health_stuck_then_recovered():
    h = ProductHealth()
    bad = _res(Status.UNKNOWN, title="")
    good = _res(Status.OUT_OF_STOCK)
    # below threshold -> quiet
    for _ in range(4):
        assert h.record(bad, threshold=5) is None
    # threshold reached -> stuck once
    assert h.record(bad, threshold=5) == "stuck"
    # already warned -> quiet
    assert h.record(bad, threshold=5) is None
    # usable read -> recovered once, streak resets
    assert h.record(good, threshold=5) == "recovered"
    assert h.unusable_streak == 0
    # subsequent usable -> quiet
    assert h.record(good, threshold=5) is None


def test_product_health_usable_keeps_quiet():
    h = ProductHealth()
    assert h.record(_res(Status.IN_STOCK), threshold=5) is None
    assert h.warned_stuck is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q -k "usable or product_health"`
Expected: FAIL — `ImportError` (`is_usable`/`ProductHealth` don't exist).

- [ ] **Step 3: Implement in `monitor.py`**

Add `Status` to the `adapters.base` import (it currently imports `DEFAULT_HEADERS, Blocked, Product` — add `Status`):

```python
from adapters.base import DEFAULT_HEADERS, Blocked, Product, Status
```

Then add, just above `class RetailerHealth:`:

```python
def is_usable(result) -> bool:
    """A check result carries usable data iff we determined a real stock status
    AND parsed a non-empty product title. Catches both UNKNOWN and the
    false-out_of_stock-with-empty-title failure mode."""
    return result.status is not Status.UNKNOWN and bool((result.title or "").strip())


class ProductHealth:
    """Per-product 'is this SKU still returning real data' tracker. In-memory
    and non-persisted, exactly like RetailerHealth (resets on restart)."""

    def __init__(self) -> None:
        self.unusable_streak = 0
        self.warned_stuck = False

    def record(self, result, threshold: int) -> str | None:
        """Returns "stuck" once when the no-usable-data streak first reaches
        `threshold`, "recovered" once when usable data returns after a stuck
        episode, else None."""
        if is_usable(result):
            was_stuck = self.warned_stuck
            self.unusable_streak = 0
            self.warned_stuck = False
            return "recovered" if was_stuck else None
        self.unusable_streak += 1
        if self.unusable_streak >= threshold and not self.warned_stuck:
            self.warned_stuck = True
            return "stuck"
        return None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: is_usable + ProductHealth product-level health tracker"
```

---

## Task 3: Wire `ProductHealth` into the monitor

**Files:**
- Modify: `monitor.py` (`process_product`, `run`, the call site)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_monitor_helpers.py`:

```python
def test_product_health_alerts_stuck_then_recovered(monkeypatch):
    import monitor
    from adapters import ADAPTERS

    class DeadAdapter:
        def __init__(self):
            self.usable = False
        async def check(self, client, prod):
            if self.usable:
                return StockResult(status=Status.OUT_OF_STOCK, title="Real Product")
            return StockResult(status=Status.UNKNOWN, title="")

    adapter = DeadAdapter()
    monkeypatch.setitem(ADAPTERS, "dead", adapter)
    monkeypatch.setattr(monitor, "save_state", lambda s: None)
    monkeypatch.setattr(monitor, "save_records", lambda r: None)

    class FakeNotifier:
        def __init__(self):
            self.sent = []
        async def send(self, embed):
            self.sent.append(embed)

    notifier = FakeNotifier()
    states, records = {}, {}
    health = defaultdict(RetailerHealth)
    product_health = defaultdict(monitor.ProductHealth)
    prod = product("dead")

    for _ in range(5):
        asyncio.run(monitor.process_product(
            None, notifier, prod, states, records, health, product_health, 5))
    blob = " ".join((e.get("title", "") + " " + e.get("description", "")) for e in notifier.sent)
    assert "no usable data" in blob.lower()

    adapter.usable = True
    notifier.sent.clear()
    asyncio.run(monitor.process_product(
        None, notifier, prod, states, records, health, product_health, 5))
    blob2 = " ".join((e.get("title", "") + " " + e.get("description", "")) for e in notifier.sent)
    assert "returning usable data again" in blob2.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_product_health_alerts_stuck_then_recovered -q`
Expected: FAIL — `process_product()` takes 6 args, not 8 (`TypeError`).

- [ ] **Step 3: Extend `process_product`**

Change the signature (add two optional params so existing 6-arg callers/tests still work):

```python
async def process_product(client, notifier, product, states, records, health,
                          product_health=None, health_threshold=5) -> bool:
```

Then, immediately after the existing recovery-notice block (the `if h.record_success():` block, right before `now = datetime.now()`), insert:

```python
    if product_health is not None:
        ph = product_health[product.key]
        ph_alert = ph.record(result, health_threshold)
        if ph_alert == "stuck":
            await notifier.send(build_system_embed(
                f"⚠️ **{product.retailer} {product.name}** has returned no usable data for "
                f"{ph.unusable_streak} checks — possibly delisted or the page changed."
            ))
        elif ph_alert == "recovered":
            await notifier.send(build_system_embed(
                f"**{product.retailer} {product.name}** is returning usable data again."
            ))
```

- [ ] **Step 4: Add the map in `run()` and pass it at the call site**

In `run()`, next to `health = defaultdict(RetailerHealth)`, add:

```python
    product_health = defaultdict(ProductHealth)
```

At the `process_product(...)` call inside the loop, pass the map + threshold from (hot-reloaded) config:

```python
                        changed = await asyncio.wait_for(
                            process_product(client, notifier, product, states, records, health,
                                            product_health, int(config.get("product_health_threshold", 5))),
                            timeout=180,
                        )
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (existing 6-arg process_product tests still pass via the defaults).

- [ ] **Step 6: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: alert when a product stops returning usable data"
```

---

## Task 4: `--check-once` usable-data summary

**Files:**
- Modify: `monitor.py` (`check_once`)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_monitor_helpers.py`:

```python
def test_check_once_flags_unusable(monkeypatch, capsys):
    import monitor
    from adapters import ADAPTERS

    class Good:
        async def check(self, c, p):
            return StockResult(status=Status.IN_STOCK, price=Decimal("50"), title="Good Box")

    class Bad:
        async def check(self, c, p):
            return StockResult(status=Status.UNKNOWN, title="")

    async def _noop():
        return None

    monkeypatch.setitem(ADAPTERS, "good", Good())
    monkeypatch.setitem(ADAPTERS, "bad", Bad())
    monkeypatch.setattr(monitor, "load_watchlist", lambda: [product("good"), product("bad")])
    monkeypatch.setattr(monitor, "shutdown_browser", _noop)

    asyncio.run(monitor.check_once())
    out = capsys.readouterr().out
    assert "NO USABLE DATA" in out
    assert "bad" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py::test_check_once_flags_unusable -q`
Expected: FAIL — no "NO USABLE DATA" summary is printed yet.

- [ ] **Step 3: Add the summary to `check_once`**

Replace the body of `check_once` with (adds an `unusable` accumulator + a final summary):

```python
async def check_once():
    products = load_watchlist()
    unusable = []
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS, follow_redirects=True, timeout=30,
            limits=HTTP_LIMITS,
        ) as client:
            for product in products:
                try:
                    result = await ADAPTERS[product.retailer].check(client, product)
                    print(f"{product.retailer:15} {product.name[:40]:40} "
                          f"{result.status.value:13} price={result.price}")
                    if not is_usable(result):
                        unusable.append(f"{product.retailer} {product.name} "
                                        f"(status={result.status.value}, title={result.title!r})")
                except Exception as exc:
                    print(f"{product.retailer:15} {product.name[:40]:40} ERROR: {exc}")
                    unusable.append(f"{product.retailer} {product.name} (ERROR: {exc})")
    finally:
        await shutdown_browser()
    print()
    if unusable:
        print(f"!!  {len(unusable)} product(s) returned NO USABLE DATA (UNKNOWN / empty title / error):")
        for u in unusable:
            print("   -", u)
    else:
        print("OK  All products returned usable data.")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_monitor_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: --check-once flags products with no usable data"
```

---

## Task 5: Add the 15 EB Games SKUs

**Files:**
- Modify: `watchlist.json`

Now that `ebgames` is browser-first (Task 1), the adapter parses these pages.

- [ ] **Step 1: Append the entries**

Run this script (appends the 15 EB Games SKUs, de-duped, preserving 2-space formatting):

```bash
python3 - <<'PY'
import json
path="watchlist.json"; w=json.load(open(path))
adds=[
 {"name":"Mega Evolution (base) Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/939896","max_price":160.0,"set":"Mega Evolution (base)","packs":36},
 {"name":"Mega Evolution (base) Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/939890/pokemon-trading-card-game-mega-evolutions-elite-trainer-box","max_price":90.0,"set":"Mega Evolution (base)","packs":9},
 {"name":"Phantasmal Flames Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/941961","max_price":160.0,"set":"Phantasmal Flames","packs":36},
 {"name":"Phantasmal Flames Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/941954","max_price":90.0,"set":"Phantasmal Flames","packs":9},
 {"name":"Ascended Heroes Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/960535/pokemon-trading-card-game-mega-evolutions-ascended-heroes-elite-trainer-box","max_price":90.0,"set":"Ascended Heroes","packs":9},
 {"name":"Perfect Order Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/962105/pokemon-trading-card-game-mega-evolution-perfect-order-booster-box","max_price":160.0,"set":"Perfect Order","packs":36},
 {"name":"Perfect Order Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/962109/pokemon-trading-card-game-mega-evolution-perfect-order-elite-trainer-box","max_price":90.0,"set":"Perfect Order","packs":9},
 {"name":"Chaos Rising Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/965103","max_price":160.0,"set":"Chaos Rising","packs":36},
 {"name":"Chaos Rising Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/965100","max_price":90.0,"set":"Chaos Rising","packs":9},
 {"name":"Pitch Black Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/967030/pokemon-trading-card-game-mega-evolution-pitch-black-booster-box","max_price":160.0,"set":"Pitch Black","packs":36},
 {"name":"Pitch Black Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/967026/pokemon-trading-card-game-mega-evolution-pitch-black-elite-trainer-box","max_price":90.0,"set":"Pitch Black","packs":9},
 {"name":"Journey Together Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/932369/pokemon-trading-card-game-scarlet-and-violet-journey-together-booster-box","max_price":160.0,"set":"Journey Together","packs":36},
 {"name":"Journey Together Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/932366/pokemon-trading-card-game-scarlet-and-violet-journey-together-elite-trainer-box","max_price":90.0,"set":"Journey Together","packs":9},
 {"name":"Surging Sparks Booster Box (36 packs)","retailer":"ebgames","url":"https://www.ebgames.ca/Toys-Collectibles/Games/926777/pok-mon-trading-card-game-scarlet-and-violet-surging-sparks-booster-box","max_price":160.0,"set":"Surging Sparks","packs":36},
 {"name":"Surging Sparks Elite Trainer Box","retailer":"ebgames","url":"https://www.ebgames.ca/Trading%20Cards/Games/926780/pok-mon-trading-card-game-scarlet-and-violet-surging-sparks-elite-trainer-box","max_price":90.0,"set":"Surging Sparks","packs":9},
]
def key(p): return f"{p['retailer']}:{p.get('sku') or p['url']}"
existing={key(p) for p in w["products"]}
new=[a for a in adds if key(a) not in existing]
w["products"].extend(new)
open(path,"w").write(json.dumps(w,indent=2)+"\n")
print(f"added {len(new)}; total {len(w['products'])}")
PY
python3 -c "import json; json.load(open('watchlist.json')); print('valid JSON')"
```
Expected: `added 15; total 76` then `valid JSON`.

- [ ] **Step 2: Live-verify a sample parses real data through the real adapter**

```bash
.venv/bin/python -c "
import asyncio, httpx
from adapters import ADAPTERS
from adapters.base import Product, DEFAULT_HEADERS
from adapters.browser import shutdown_browser
from decimal import Decimal
async def check(url):
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=30) as c:
        r = await ADAPTERS['ebgames'].check(c, Product(name='x', retailer='ebgames', url=url, max_price=Decimal('1')))
        print('status=', r.status.value, '| title=', repr(r.title[:50]), '| price=', r.price)
async def main():
    for u in [
      'https://www.ebgames.ca/Trading%20Cards/Games/965103',
      'https://www.ebgames.ca/Trading%20Cards/Games/939890/pokemon-trading-card-game-mega-evolutions-elite-trainer-box',
      'https://www.ebgames.ca/Trading%20Cards/Games/962105/pokemon-trading-card-game-mega-evolution-perfect-order-booster-box',
    ]:
        await check(u)
    await shutdown_browser()
asyncio.run(main())
"
```
Expected: each line shows a real `status` (in_stock/out_of_stock, NOT unknown) AND a non-empty `title`. (Windows will flash — headed Chrome.) If any come back `unknown`/empty, STOP and report — the browser-first fix isn't yielding JSON-LD for that page.

- [ ] **Step 3: Commit**

```bash
git add watchlist.json
git commit -m "feat: add 15 EB Games SKUs (now that the adapter browser-renders)"
```

---

## Deployment (post-merge, on `pmonitor`)

Code change (new EB Games adapter behaviour + health guard) requires a restart to load:

```bash
cd ~/pokemon-monitor && git pull
launchctl kickstart -k gui/$(id -u)/com.pokemonmonitor
```

Optional: run `.venv/bin/python monitor.py --check-once` once to confirm the summary reports "All products returned usable data" (or names any stragglers). `product_health_threshold` can be added to `config.json` to override the default of 5.

---

## Self-Review

- **Spec coverage:** EB Games browser-first → Task 1; 15 SKUs → Task 5; `--check-once` summary → Task 4; `is_usable` + `ProductHealth` + wiring → Tasks 2–3; product-health config threshold → Task 3 (`product_health_threshold`). All covered.
- **Type consistency:** `is_usable(result)->bool`, `ProductHealth.record(result, threshold)->str|None` with `unusable_streak`/`warned_stuck`, `process_product(..., product_health=None, health_threshold=5)`, `JsonLdAdapter(browser_first=False)` used consistently across tasks.
- **Placeholders:** none — every step has concrete code/commands; EB Games URLs are the audited, page-verified ones.
