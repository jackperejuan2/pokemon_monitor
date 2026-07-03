# Pokemon Card Restock Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 24/7 monitor on the user's Mac that checks seven Canadian retailers for Pokemon card restocks and fires a Discord webhook alert when a watched product is in stock at or below its max price (MSRP, CAD).

**Architecture:** One async Python process. A scheduler loop dispatches per-product checks to per-retailer adapters (HTTP-first via `httpx`; Playwright for Pokemon Center). A pure state-transition function (`state.decide`) turns check results into at-most-one alert per restock. Discord webhook notifier. JSON files for watchlist/config/state. `launchd` keeps it alive.

**Tech Stack:** Python 3.12, httpx, Playwright (Chromium), pytest. No database.

**Spec:** `docs/superpowers/specs/2026-07-03-pokemon-restock-monitor-design.md`

**Deviation from spec (intentional, DRY):** the spec lists separate adapter files for Toys"R"Us, Indigo, EB Games, and Costco. All four use the identical JSON-LD strategy, so they share one `JsonLdAdapter` class in `adapters/jsonld.py`, registered four times in the adapter registry. Retailer-specific quirks discovered during live validation can split them into their own files later.

**Live-site caveat:** endpoint shapes and page structures in this plan are best-known guesses; Task 12 validates every adapter against the real sites and adjusts parsers. Unit tests use synthetic fixtures that encode the expected shape.

---

## File Structure

```
Pokemon Monitor/                  (repo root)
├── requirements.txt
├── pyproject.toml                # pytest config only
├── .gitignore
├── config.example.json           # committed template
├── config.json                   # real webhook URL — gitignored
├── watchlist.json                # user's products — committed
├── monitor.py                    # scheduler loop, backoff, quiet hours, CLI
├── state.py                      # ProductState, decide(), load/save state.json
├── notifier.py                   # Discord embeds + webhook sender
├── adapters/
│   ├── __init__.py               # ADAPTERS registry
│   ├── base.py                   # Status, Product, StockResult, Blocked, headers
│   ├── jsonld.py                 # shared JSON-LD page parser + JsonLdAdapter
│   ├── bestbuy.py                # BestBuy.ca availability + offers APIs
│   ├── walmart.py                # Walmart.ca __NEXT_DATA__ parser
│   └── pokemoncenter.py          # Playwright adapter
├── launchd/com.pokemonmonitor.plist
├── README.md
└── tests/
    ├── test_base.py
    ├── test_jsonld.py
    ├── test_bestbuy.py
    ├── test_walmart.py
    ├── test_pokemoncenter.py
    ├── test_notifier.py
    ├── test_state.py
    └── test_monitor_helpers.py
```

---

### Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `.gitignore`, `config.example.json`, `adapters/__init__.py` (empty for now), `tests/` (dir)

- [ ] **Step 1: Create requirements.txt**

```
httpx>=0.27
playwright>=1.45
pytest>=8.0
```

- [ ] **Step 2: Create pyproject.toml** (pytest needs repo root on sys.path since we don't install a package)

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 3: Create .gitignore**

```
.venv/
__pycache__/
*.pyc
state.json
config.json
logs/
```

- [ ] **Step 4: Create config.example.json**

```json
{
  "discord_webhook_url": "https://discord.com/api/webhooks/CHANGE_ME",
  "check_interval_seconds": [120, 300],
  "pokemoncenter_interval_seconds": [600, 900],
  "quiet_hours": {"start": "01:30", "end": "07:00"},
  "heartbeat_hour": 9
}
```

- [ ] **Step 5: Create empty `adapters/__init__.py` and `tests/` directory, then set up venv**

```bash
mkdir -p adapters tests logs launchd
touch adapters/__init__.py
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

Expected: pip and playwright install succeed. Verify: `.venv/bin/python -c "import httpx, playwright; print('ok')"` prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pyproject.toml .gitignore config.example.json adapters/__init__.py
git commit -m "chore: project scaffold"
```

---

### Task 2: Core types (`adapters/base.py`)

**Files:**
- Create: `adapters/base.py`
- Test: `tests/test_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base.py
from decimal import Decimal

from adapters.base import Blocked, Product, Status, StockResult, raise_if_blocked
import httpx
import pytest


def test_product_key_uses_sku_when_present():
    p = Product(name="ETB", retailer="bestbuy", url="https://x/y", max_price=Decimal("64.99"), sku="123")
    assert p.key == "bestbuy:123"


def test_product_key_falls_back_to_url():
    p = Product(name="ETB", retailer="costco", url="https://x/y", max_price=Decimal("64.99"))
    assert p.key == "costco:https://x/y"


def test_stock_result_defaults():
    r = StockResult(status=Status.OUT_OF_STOCK)
    assert r.price is None and r.title == "" and r.url == ""


@pytest.mark.parametrize("code", [403, 429])
def test_raise_if_blocked_raises_on_block_codes(code):
    resp = httpx.Response(code, request=httpx.Request("GET", "https://www.example.ca/p"))
    with pytest.raises(Blocked):
        raise_if_blocked(resp)


def test_raise_if_blocked_passes_normal_responses():
    resp = httpx.Response(200, request=httpx.Request("GET", "https://www.example.ca/p"))
    raise_if_blocked(resp)  # no exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_base.py -v`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` (base.py doesn't exist).

- [ ] **Step 3: Write implementation**

```python
# adapters/base.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

import httpx


class Status(Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


@dataclass
class Product:
    name: str
    retailer: str
    url: str
    max_price: Decimal
    sku: str | None = None

    @property
    def key(self) -> str:
        return f"{self.retailer}:{self.sku or self.url}"


@dataclass
class StockResult:
    status: Status
    price: Decimal | None = None
    title: str = ""
    url: str = ""


class Blocked(Exception):
    """Retailer returned a bot-block response (403/429/challenge page)."""


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


class Adapter(Protocol):
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult: ...


def raise_if_blocked(response: httpx.Response) -> None:
    if response.status_code in (403, 429):
        raise Blocked(f"{response.request.url.host} returned {response.status_code}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_base.py -v`
Expected: 6 passed (the parametrized block test runs twice).

- [ ] **Step 5: Commit**

```bash
git add adapters/base.py tests/test_base.py
git commit -m "feat: core adapter types (Status, Product, StockResult, Blocked)"
```

---

### Task 3: Shared JSON-LD parser + adapter (`adapters/jsonld.py`)

Covers Toys"R"Us CA, Indigo, EB Games, Costco.ca.

**Files:**
- Create: `adapters/jsonld.py`
- Test: `tests/test_jsonld.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jsonld.py
from decimal import Decimal

from adapters.base import Status
from adapters.jsonld import parse_stock_from_html

IN_STOCK_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Pokemon TCG Prismatic Evolutions ETB",
 "offers":{"@type":"Offer","price":"64.99","priceCurrency":"CAD",
 "availability":"https://schema.org/InStock"}}
</script>
</head><body><button>Add to Cart</button></body></html>
"""

OUT_OF_STOCK_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":"Product","name":"ETB","offers":[{"price":64.99,
 "availability":"http://schema.org/OutOfStock"}]}
</script>
</head><body>Sold out</body></html>
"""

NESTED_GRAPH_HTML = """
<html><head>
<script type="application/ld+json">
{"@graph":[{"@type":"WebPage"},{"@type":"Product","name":"ETB",
 "offers":{"price":"59.99","availability":"https://schema.org/InStock"}}]}
</script>
</head></html>
"""

NO_LDJSON_OOS_HTML = "<html><body><p>This item is currently SOLD OUT</p></body></html>"
NO_LDJSON_ATC_HTML = "<html><body><button id='atc'>Add to cart</button></body></html>"
NO_SIGNAL_HTML = "<html><body><p>welcome</p></body></html>"
BAD_JSON_HTML = '<html><script type="application/ld+json">{not json</script><body>Add to cart</body></html>'


def test_parses_in_stock_offer():
    r = parse_stock_from_html(IN_STOCK_HTML, "https://x/p")
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.99")
    assert r.title == "Pokemon TCG Prismatic Evolutions ETB"
    assert r.url == "https://x/p"


def test_parses_out_of_stock_offer_list():
    r = parse_stock_from_html(OUT_OF_STOCK_HTML)
    assert r.status is Status.OUT_OF_STOCK


def test_finds_product_inside_graph():
    r = parse_stock_from_html(NESTED_GRAPH_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("59.99")


def test_fallback_out_of_stock_marker():
    assert parse_stock_from_html(NO_LDJSON_OOS_HTML).status is Status.OUT_OF_STOCK


def test_fallback_add_to_cart_marker_has_no_price():
    r = parse_stock_from_html(NO_LDJSON_ATC_HTML)
    assert r.status is Status.IN_STOCK
    assert r.price is None


def test_no_signal_is_unknown():
    assert parse_stock_from_html(NO_SIGNAL_HTML).status is Status.UNKNOWN


def test_malformed_json_falls_back_to_markers():
    assert parse_stock_from_html(BAD_JSON_HTML).status is Status.IN_STOCK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_jsonld.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adapters.jsonld'`.

- [ ] **Step 3: Write implementation**

```python
# adapters/jsonld.py
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Iterator

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
OOS_MARKERS = ("out of stock", "sold out", "currently unavailable")
ATC_MARKERS = ("add to cart", "add to bag")


def parse_stock_from_html(html: str, url: str = "") -> StockResult:
    for match in LDJSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for node in _product_nodes(data):
            result = _result_from_product_node(node, url)
            if result is not None:
                return result
    return _fallback_from_markers(html, url)


def _product_nodes(data: object) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _product_nodes(item)
    elif isinstance(data, dict):
        if data.get("@type") == "Product":
            yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from _product_nodes(value)


def _result_from_product_node(node: dict, url: str) -> StockResult | None:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None
    availability = str(offers.get("availability", "")).lower()
    if "instock" in availability:
        status = Status.IN_STOCK
    elif "outofstock" in availability or "soldout" in availability:
        status = Status.OUT_OF_STOCK
    else:
        return None
    price: Decimal | None = None
    raw_price = offers.get("price")
    if raw_price is not None:
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation:
            price = None
    return StockResult(status=status, price=price, title=node.get("name", ""), url=url)


def _fallback_from_markers(html: str, url: str) -> StockResult:
    lowered = html.lower()
    if any(m in lowered for m in OOS_MARKERS):
        return StockResult(status=Status.OUT_OF_STOCK, url=url)
    if any(m in lowered for m in ATC_MARKERS):
        return StockResult(status=Status.IN_STOCK, url=url)  # price unknown
    return StockResult(status=Status.UNKNOWN, url=url)


class JsonLdAdapter:
    """Generic adapter for retailers whose product pages carry schema.org JSON-LD."""

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(product.url)
        raise_if_blocked(response)
        response.raise_for_status()
        return parse_stock_from_html(response.text, product.url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_jsonld.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/jsonld.py tests/test_jsonld.py
git commit -m "feat: shared JSON-LD stock parser and adapter"
```

---

### Task 4: BestBuy.ca adapter (`adapters/bestbuy.py`)

**Files:**
- Create: `adapters/bestbuy.py`
- Test: `tests/test_bestbuy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bestbuy.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_bestbuy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adapters.bestbuy'`.

- [ ] **Step 3: Write implementation**

```python
# adapters/bestbuy.py
from __future__ import annotations

import json
from decimal import Decimal

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

AVAILABILITY_URL = "https://www.bestbuy.ca/ecomm-api/availability/products"
OFFERS_URL = "https://www.bestbuy.ca/api/offers/v1/products/{sku}/offers"


def parse_availability(payload: dict) -> Status:
    availabilities = payload.get("availabilities") or []
    if not availabilities:
        return Status.UNKNOWN
    shipping = availabilities[0].get("shipping") or {}
    purchasable = shipping.get("purchasable", False)
    status_text = str(shipping.get("status", "")).lower()
    if purchasable and "instock" in status_text:
        return Status.IN_STOCK
    return Status.OUT_OF_STOCK


def parse_price(offers: list) -> Decimal | None:
    if not offers:
        return None
    offer = offers[0]
    raw = offer.get("salePrice") or offer.get("regularPrice")
    return Decimal(str(raw)) if raw is not None else None


class BestBuyAdapter:
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(
            AVAILABILITY_URL,
            params={
                "accept": "application/vnd.bestbuy.standardproduct.v1+json",
                "skus": product.sku,
            },
        )
        raise_if_blocked(response)
        response.raise_for_status()
        # BestBuy.ca prepends a UTF-8 BOM to this endpoint's JSON
        payload = json.loads(response.text.lstrip("\ufeff"))
        status = parse_availability(payload)

        price: Decimal | None = None
        if status is Status.IN_STOCK:
            offers_resp = await client.get(OFFERS_URL.format(sku=product.sku))
            if offers_resp.status_code == 200:
                price = parse_price(offers_resp.json())
        return StockResult(status=status, price=price, title=product.name, url=product.url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_bestbuy.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/bestbuy.py tests/test_bestbuy.py
git commit -m "feat: BestBuy.ca availability adapter"
```

---

### Task 5: Walmart.ca adapter (`adapters/walmart.py`)

**Files:**
- Create: `adapters/walmart.py`
- Test: `tests/test_walmart.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_walmart.py
import json
from decimal import Decimal

from adapters.base import Status
from adapters.walmart import parse_next_data


def make_html(availability="IN_STOCK", price=64.97, name="Pokemon ETB"):
    payload = {
        "props": {
            "pageProps": {
                "initialData": {
                    "data": {
                        "product": {
                            "name": name,
                            "availabilityStatus": availability,
                            "priceInfo": {"currentPrice": {"price": price}},
                        }
                    }
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></html>"
    )


def test_in_stock():
    r = parse_next_data(make_html(), "https://walmart.ca/p")
    assert r.status is Status.IN_STOCK
    assert r.price == Decimal("64.97")
    assert r.title == "Pokemon ETB"
    assert r.url == "https://walmart.ca/p"


def test_out_of_stock():
    r = parse_next_data(make_html(availability="OUT_OF_STOCK"))
    assert r.status is Status.OUT_OF_STOCK


def test_missing_next_data_is_unknown():
    assert parse_next_data("<html>no data</html>").status is Status.UNKNOWN


def test_unexpected_shape_is_unknown():
    html = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{}}</script></html>'
    assert parse_next_data(html).status is Status.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_walmart.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adapters.walmart'`.

- [ ] **Step 3: Write implementation**

```python
# adapters/walmart.py
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def parse_next_data(html: str, url: str = "") -> StockResult:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return StockResult(status=Status.UNKNOWN, url=url)
    try:
        data = json.loads(match.group(1))
        product = data["props"]["pageProps"]["initialData"]["data"]["product"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return StockResult(status=Status.UNKNOWN, url=url)

    availability = str(product.get("availabilityStatus", "")).upper()
    if availability == "IN_STOCK":
        status = Status.IN_STOCK
    elif availability:
        status = Status.OUT_OF_STOCK
    else:
        status = Status.UNKNOWN

    price: Decimal | None = None
    raw = (product.get("priceInfo") or {}).get("currentPrice") or {}
    if raw.get("price") is not None:
        try:
            price = Decimal(str(raw["price"]))
        except InvalidOperation:
            price = None
    return StockResult(status=status, price=price, title=product.get("name", ""), url=url)


class WalmartAdapter:
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(product.url)
        raise_if_blocked(response)
        response.raise_for_status()
        return parse_next_data(response.text, product.url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_walmart.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/walmart.py tests/test_walmart.py
git commit -m "feat: Walmart.ca __NEXT_DATA__ adapter"
```

---

### Task 6: Pokemon Center adapter (`adapters/pokemoncenter.py`)

Playwright-based; reuses `parse_stock_from_html` on the rendered page. Best-effort in v1 per spec.

**Files:**
- Create: `adapters/pokemoncenter.py`
- Test: `tests/test_pokemoncenter.py`

- [ ] **Step 1: Write the failing test** (only the pure challenge-detection helper is unit-testable; the browser path is validated live in Task 12)

```python
# tests/test_pokemoncenter.py
import pytest

from adapters.pokemoncenter import is_challenge_page


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>Access Denied</body></html>",
        "<html><script src='/_Incapsula_Resource?x=1'></script></html>",
        "<html><body>Pardon Our Interruption</body></html>",
    ],
)
def test_detects_challenge_pages(html):
    assert is_challenge_page(html)


def test_normal_page_is_not_challenge():
    assert not is_challenge_page("<html><body>Pokemon TCG: Add to Cart $64.99</body></html>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pokemoncenter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adapters.pokemoncenter'`.

- [ ] **Step 3: Write implementation**

```python
# adapters/pokemoncenter.py
from __future__ import annotations

from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from .base import Blocked, Product, StockResult
from .jsonld import parse_stock_from_html

PROFILE_DIR = Path.home() / ".pokemon-monitor" / "pc-profile"
CHALLENGE_MARKERS = ("access denied", "_incapsula_", "pardon our interruption", "captcha")


def is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


class PokemonCenterAdapter:
    """Loads the product page in a real (headed) Chromium with a persistent
    profile, then reuses the JSON-LD/marker parser on the rendered HTML."""

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        # `client` is unused: Pokemon Center blocks plain HTTP.
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="en-CA",
            )
            try:
                page = await context.new_page()
                await page.goto(product.url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(5_000)  # let JS/anti-bot settle
                html = await page.content()
            finally:
                await context.close()
        if is_challenge_page(html):
            raise Blocked("pokemoncenter served a challenge page")
        return parse_stock_from_html(html, product.url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pokemoncenter.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/pokemoncenter.py tests/test_pokemoncenter.py
git commit -m "feat: Pokemon Center Playwright adapter"
```

---

### Task 7: Adapter registry (`adapters/__init__.py`)

**Files:**
- Modify: `adapters/__init__.py`
- Test: `tests/test_base.py` (append)

- [ ] **Step 1: Append the failing test to tests/test_base.py**

```python
# append to tests/test_base.py
def test_registry_covers_all_seven_retailers():
    from adapters import ADAPTERS

    assert set(ADAPTERS) == {
        "bestbuy", "walmart", "toysrus", "indigo", "ebgames", "costco", "pokemoncenter",
    }
    for adapter in ADAPTERS.values():
        assert callable(getattr(adapter, "check", None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_base.py::test_registry_covers_all_seven_retailers -v`
Expected: FAIL with `ImportError: cannot import name 'ADAPTERS'`.

- [ ] **Step 3: Write implementation**

```python
# adapters/__init__.py
from .base import Adapter
from .bestbuy import BestBuyAdapter
from .jsonld import JsonLdAdapter
from .pokemoncenter import PokemonCenterAdapter
from .walmart import WalmartAdapter

ADAPTERS: dict[str, Adapter] = {
    "bestbuy": BestBuyAdapter(),
    "walmart": WalmartAdapter(),
    "toysrus": JsonLdAdapter(),
    "indigo": JsonLdAdapter(),
    "ebgames": JsonLdAdapter(),
    "costco": JsonLdAdapter(),
    "pokemoncenter": PokemonCenterAdapter(),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_base.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add adapters/__init__.py tests/test_base.py
git commit -m "feat: adapter registry for all seven retailers"
```

---

### Task 8: Discord notifier (`notifier.py`)

**Files:**
- Create: `notifier.py`
- Test: `tests/test_notifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notifier.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notifier'`.

- [ ] **Step 3: Write implementation**

```python
# notifier.py
from __future__ import annotations

import httpx

from adapters.base import Product, StockResult

COLOR_RESTOCK = 0x2ECC71      # green
COLOR_OVER_PRICE = 0xF1C40F   # yellow
COLOR_SYSTEM = 0xE74C3C       # red
COLOR_INFO = 0x95A5A6         # grey


def build_restock_embed(product: Product, result: StockResult) -> dict:
    return {
        "title": f"🟢 RESTOCK: {product.name}",
        "url": product.url,
        "color": COLOR_RESTOCK,
        "description": (
            f"**${result.price} CAD** (max ${product.max_price}) at **{product.retailer}**\n"
            f"[Buy now]({product.url})"
        ),
    }


def build_over_price_embed(product: Product, result: StockResult) -> dict:
    price_text = f"${result.price} CAD" if result.price is not None else "price unknown"
    return {
        "title": f"🟡 In stock over max: {product.name}",
        "url": product.url,
        "color": COLOR_OVER_PRICE,
        "description": f"{price_text} (max ${product.max_price}) at {product.retailer}",
    }


def build_system_embed(message: str) -> dict:
    return {"title": "⚠️ Monitor notice", "color": COLOR_SYSTEM, "description": message}


def build_heartbeat_embed(product_count: int, unhealthy: list[str]) -> dict:
    if unhealthy:
        detail = "degraded adapters: " + ", ".join(unhealthy)
    else:
        detail = "all adapters healthy"
    return {
        "title": "✅ Daily heartbeat",
        "color": COLOR_INFO,
        "description": f"Monitoring {product_count} products, {detail}.",
    }


class Notifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, embed: dict) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(self.webhook_url, json={"embeds": [embed]})
            response.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add notifier.py tests/test_notifier.py
git commit -m "feat: Discord webhook notifier with embed builders"
```

---

### Task 9: State machine (`state.py`)

The heart of alert correctness: exactly one alert per restock, over-price notices at most daily, transient UNKNOWNs don't wipe known state.

**Files:**
- Create: `state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Write implementation**

```python
# state.py
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from adapters.base import Product, Status, StockResult

STATE_PATH = Path(__file__).parent / "state.json"
OVER_PRICE_COOLDOWN = timedelta(hours=24)


@dataclass
class ProductState:
    status: str = "unknown"
    price_ok: bool = False
    last_over_price_alert: str | None = None


@dataclass
class Decision:
    new_state: ProductState
    alert: str | None = None  # "restock" | "over_price" | None


def decide(prev: ProductState, result: StockResult, product: Product, now: datetime) -> Decision:
    if result.status is Status.UNKNOWN:
        # Transient parse/availability failure: keep what we knew, stay quiet.
        return Decision(ProductState(**asdict(prev)), None)

    price_ok = (
        result.status is Status.IN_STOCK
        and result.price is not None
        and result.price <= product.max_price
    )
    new = ProductState(
        status=result.status.value,
        price_ok=price_ok,
        last_over_price_alert=prev.last_over_price_alert,
    )

    if price_ok and not prev.price_ok:
        return Decision(new, "restock")
    if result.status is Status.IN_STOCK and not price_ok and _over_price_due(prev.last_over_price_alert, now):
        new.last_over_price_alert = now.isoformat()
        return Decision(new, "over_price")
    return Decision(new, None)


def _over_price_due(last_iso: str | None, now: datetime) -> bool:
    if last_iso is None:
        return True
    return now - datetime.fromisoformat(last_iso) >= OVER_PRICE_COOLDOWN


def load_state() -> dict[str, ProductState]:
    if not STATE_PATH.exists():
        return {}
    raw = json.loads(STATE_PATH.read_text())
    return {key: ProductState(**value) for key, value in raw.items()}


def save_state(states: dict[str, ProductState]) -> None:
    STATE_PATH.write_text(
        json.dumps({key: asdict(value) for key, value in states.items()}, indent=2)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add state.py tests/test_state.py
git commit -m "feat: alert state machine with dedup and over-price cooldown"
```

---

### Task 10: Monitor helpers — quiet hours, backoff, intervals

**Files:**
- Create: `monitor.py` (helpers only; the main loop comes in Task 11)
- Test: `tests/test_monitor_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_monitor_helpers.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_monitor_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'monitor'`.

- [ ] **Step 3: Write implementation** (helpers only)

```python
# monitor.py
"""Pokemon card restock monitor.

Usage:
    python monitor.py                # run forever
    python monitor.py --check-once   # check every product once and print results
"""
from __future__ import annotations

import json
import random
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path

from adapters.base import Product

BASE_DIR = Path(__file__).parent
WATCHLIST_PATH = BASE_DIR / "watchlist.json"
CONFIG_PATH = BASE_DIR / "config.json"


class RetailerHealth:
    MAX_BACKOFF = 16.0  # x base interval; ~300s * 16 = 80 min worst case

    def __init__(self) -> None:
        self.backoff = 1.0
        self.consecutive_errors = 0
        self.warned_blocked = False
        self.warned_errors = False

    def record_blocked(self) -> bool:
        """Returns True exactly once per blocked episode (caller should warn)."""
        self.backoff = min(self.backoff * 2, self.MAX_BACKOFF)
        first = not self.warned_blocked
        self.warned_blocked = True
        return first

    def record_error(self) -> bool:
        """Returns True exactly once, at the 5th consecutive error."""
        self.consecutive_errors += 1
        if self.consecutive_errors == 5 and not self.warned_errors:
            self.warned_errors = True
            return True
        return False

    def record_success(self) -> None:
        self.backoff = 1.0
        self.consecutive_errors = 0
        self.warned_blocked = False
        self.warned_errors = False


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def load_watchlist() -> list[Product]:
    data = json.loads(WATCHLIST_PATH.read_text())
    return [
        Product(
            name=entry["name"],
            retailer=entry["retailer"],
            url=entry["url"],
            max_price=Decimal(str(entry["max_price"])),
            sku=entry.get("sku"),
        )
        for entry in data["products"]
    ]


def in_quiet_hours(now: datetime, config: dict) -> bool:
    quiet = config.get("quiet_hours")
    if not quiet:
        return False
    start = dtime.fromisoformat(quiet["start"])
    end = dtime.fromisoformat(quiet["end"])
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # wraps midnight


def check_interval(product: Product, config: dict, health: RetailerHealth) -> float:
    key = (
        "pokemoncenter_interval_seconds"
        if product.retailer == "pokemoncenter"
        else "check_interval_seconds"
    )
    low, high = config.get(key, [120, 300])
    return random.uniform(low, high) * health.backoff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_monitor_helpers.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor.py tests/test_monitor_helpers.py
git commit -m "feat: scheduling helpers - quiet hours, backoff, jittered intervals"
```

---

### Task 11: Main loop and `--check-once` (`monitor.py`)

**Files:**
- Modify: `monitor.py` (append main loop below the Task 10 helpers)

- [ ] **Step 1: Append the main loop to monitor.py**

```python
# append to monitor.py
import argparse
import asyncio
import logging
from collections import defaultdict

import httpx

from adapters import ADAPTERS
from adapters.base import DEFAULT_HEADERS, Blocked
from notifier import (
    Notifier,
    build_heartbeat_embed,
    build_over_price_embed,
    build_restock_embed,
    build_system_embed,
)
from state import ProductState, decide, load_state, save_state

log = logging.getLogger("monitor")


async def process_product(
    client: httpx.AsyncClient,
    notifier: Notifier,
    product: Product,
    states: dict[str, ProductState],
    health: dict[str, RetailerHealth],
) -> None:
    h = health[product.retailer]
    try:
        result = await ADAPTERS[product.retailer].check(client, product)
    except Blocked as exc:
        log.warning("%s blocked: %s", product.retailer, exc)
        if h.record_blocked():
            await notifier.send(
                build_system_embed(f"**{product.retailer}** is blocking checks ({exc}). Backing off.")
            )
        return
    except Exception as exc:
        log.exception("%s check failed for %s", product.retailer, product.name)
        if h.record_error():
            await notifier.send(
                build_system_embed(f"**{product.retailer}** has failed 5 checks in a row: {exc}")
            )
        return

    h.record_success()
    prev = states.get(product.key, ProductState())
    decision = decide(prev, result, product, datetime.now())
    states[product.key] = decision.new_state
    save_state(states)
    log.info("%s %s -> %s price=%s alert=%s",
             product.retailer, product.name, result.status.value, result.price, decision.alert)
    if decision.alert == "restock":
        await notifier.send(build_restock_embed(product, result))
    elif decision.alert == "over_price":
        await notifier.send(build_over_price_embed(product, result))


def unhealthy_retailers(health: dict[str, RetailerHealth]) -> list[str]:
    return sorted(
        name for name, h in health.items()
        if h.warned_blocked or h.consecutive_errors >= 5
    )


async def run() -> None:
    config = load_config()
    notifier = Notifier(config["discord_webhook_url"])
    states = load_state()
    health: dict[str, RetailerHealth] = defaultdict(RetailerHealth)
    next_check: dict[str, datetime] = {}
    last_heartbeat_date = None

    await notifier.send(build_system_embed("Monitor started."))

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS, follow_redirects=True, timeout=30
    ) as client:
        while True:
            config = load_config()          # hot-reload
            products = load_watchlist()     # hot-reload
            now = datetime.now()

            if in_quiet_hours(now, config):
                await asyncio.sleep(60)
                continue

            heartbeat_hour = config.get("heartbeat_hour", 9)
            if now.hour >= heartbeat_hour and last_heartbeat_date != now.date():
                last_heartbeat_date = now.date()
                await notifier.send(
                    build_heartbeat_embed(len(products), unhealthy_retailers(health))
                )

            for product in products:
                if now < next_check.get(product.key, now):
                    continue
                await process_product(client, notifier, product, states, health)
                next_check[product.key] = datetime.now() + timedelta(
                    seconds=check_interval(product, config, health[product.retailer])
                )
                await asyncio.sleep(random.uniform(2, 8))  # spread checks out
                now = datetime.now()

            await asyncio.sleep(5)


async def check_once() -> None:
    products = load_watchlist()
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS, follow_redirects=True, timeout=30
    ) as client:
        for product in products:
            try:
                result = await ADAPTERS[product.retailer].check(client, product)
                print(f"{product.retailer:15} {product.name[:40]:40} "
                      f"{result.status.value:13} price={result.price}")
            except Exception as exc:
                print(f"{product.retailer:15} {product.name[:40]:40} ERROR: {exc}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-once", action="store_true",
                        help="check every watchlist product once, print results, exit")
    args = parser.parse_args()
    asyncio.run(check_once() if args.check_once else run())


if __name__ == "__main__":
    main()
```

(`timedelta` is already imported at the top of `monitor.py` from Task 10.)

- [ ] **Step 2: Run the full test suite to confirm nothing broke**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (helpers unchanged; main loop has no unit tests — it is exercised via `--check-once` in Task 12).

- [ ] **Step 3: Sanity-check the CLI parses**

Run: `.venv/bin/python monitor.py --help`
Expected: usage text showing `--check-once`, exit 0.

- [ ] **Step 4: Commit**

```bash
git add monitor.py
git commit -m "feat: main scheduler loop, heartbeat, and --check-once mode"
```

---

### Task 12: Live validation against real sites

This task is interactive and iterative — it needs the user's Discord webhook URL and real product URLs/SKUs, and adapters WILL need adjustment once they meet real responses.

**Files:**
- Create: `watchlist.json`, `config.json` (from `config.example.json`)

- [ ] **Step 1: Get inputs from the user**

Ask the user for:
1. Their Discord webhook URL (Server Settings → Integrations → Webhooks → New Webhook → Copy URL).
2. 1–3 real products they want to watch, ideally covering several retailers (product page URL, and for Best Buy the SKU from the URL/page, plus max price in CAD).

Create `config.json` (copy of `config.example.json` with the real webhook URL) and `watchlist.json`:

```json
{
  "products": [
    {
      "name": "EXAMPLE - replace with real product",
      "retailer": "bestbuy",
      "sku": "17095567",
      "url": "https://www.bestbuy.ca/en-ca/product/17095567",
      "max_price": 64.99
    }
  ]
}
```

- [ ] **Step 2: Test the Discord webhook**

Run: `.venv/bin/python -c "
import asyncio
from notifier import Notifier, build_system_embed
import json
config = json.load(open('config.json'))
asyncio.run(Notifier(config['discord_webhook_url']).send(build_system_embed('Webhook test — hello from the monitor!')))
print('sent')
"`
Expected: `sent` printed and the message appears in the user's Discord channel. Confirm with the user.

- [ ] **Step 3: Run the live smoke test**

Run: `.venv/bin/python monitor.py --check-once`
Expected: one line per product with a real status and price. `UNKNOWN` status or `ERROR` lines mean that adapter needs adjustment.

- [ ] **Step 4: Fix any adapter that failed against the real site**

For each failing retailer: fetch the real response, save it as a fixture under `tests/fixtures/<retailer>_<state>.html` (or `.json`), add a regression test loading that fixture, then adjust the parser until the fixture test passes. Re-run `--check-once` to confirm live. This is expected work, not an anomaly — especially for Pokemon Center and Walmart. If a retailer hard-blocks even correct requests, document it in README as degraded and move on (spec: no bot-evasion arms race).

- [ ] **Step 5: Run full suite and commit**

Run: `.venv/bin/pytest -v` — expected: all pass.

```bash
git add watchlist.json tests/
git commit -m "feat: live-validated adapters with real watchlist"
```

(`config.json` stays untracked — it holds the webhook URL.)

---

### Task 13: launchd service + README

**Files:**
- Create: `launchd/com.pokemonmonitor.plist`, `README.md`

- [ ] **Step 1: Create the launchd plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.pokemonmonitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/northandunder/Documents/Pokemon Monitor/.venv/bin/python</string>
    <string>/Users/northandunder/Documents/Pokemon Monitor/monitor.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/northandunder/Documents/Pokemon Monitor</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/northandunder/Documents/Pokemon Monitor/logs/monitor.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/northandunder/Documents/Pokemon Monitor/logs/monitor.err.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Install and start the service**

```bash
mkdir -p logs
cp launchd/com.pokemonmonitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pokemonmonitor.plist
sleep 5
launchctl list | grep pokemonmonitor
tail -5 logs/monitor.log
```

Expected: `launchctl list` shows the job with a PID; the log shows startup lines; a "Monitor started." message appears in Discord.

- [ ] **Step 3: Write README.md**

```markdown
# Pokemon Card Restock Monitor

Watches Canadian retailers (Best Buy, Walmart, Toys"R"Us, Indigo, EB Games,
Costco, Pokemon Center) and posts a Discord alert when a watched product is in
stock at or below your max price (CAD). You buy manually — this tool never
auto-purchases.

## Adding / removing products

Edit `watchlist.json` — the monitor hot-reloads it, no restart needed:

    {
      "name": "Prismatic Evolutions ETB",
      "retailer": "bestbuy",        // bestbuy | walmart | toysrus | indigo |
                                    // ebgames | costco | pokemoncenter
      "sku": "17095567",            // required for bestbuy, optional otherwise
      "url": "https://www.bestbuy.ca/en-ca/product/17095567",
      "max_price": 64.99
    }

## Operations

    .venv/bin/python monitor.py --check-once      # test every product now
    tail -f logs/monitor.log                       # watch activity
    launchctl unload ~/Library/LaunchAgents/com.pokemonmonitor.plist   # stop
    launchctl load ~/Library/LaunchAgents/com.pokemonmonitor.plist     # start

## Behavior notes

- Alerts: green = restock at/under max (buy!), yellow = in stock but over max
  (max once/day), red = system notice, grey = daily heartbeat. No heartbeat =
  the monitor (or the Mac) is down.
- Quiet hours and check intervals are in `config.json` (hot-reloaded).
- If a retailer blocks us, checks back off automatically (up to ~80 min) and
  you get one Discord warning. No CAPTCHA solving by design.
- Pokemon Center checks open a real Chromium window briefly (~every 10 min);
  this is required to get past its bot protection and is best-effort.
- Your Mac must be awake for checks to run: System Settings → prevent sleep,
  or run `caffeinate -s` — otherwise expect gaps.

## Development

    .venv/bin/pytest -v
```

- [ ] **Step 4: Commit**

```bash
git add launchd/com.pokemonmonitor.plist README.md
git commit -m "feat: launchd service and README"
```

---

## Post-plan verification

After all tasks: run `.venv/bin/pytest -v` (all green), confirm a restock alert fires end-to-end by temporarily setting a watched product's `max_price` above a currently-in-stock item's price and deleting `state.json` (it will alert on the next check), then restore the real max price.
