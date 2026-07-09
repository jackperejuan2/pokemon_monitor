# Check Health — EB Games fix + product-level health guard

**Date:** 2026-07-08
**Status:** Approved design, pending implementation

## Problem

The coverage audit exposed a silent-failure class: a watchlist product can be
"checked" every cycle yet return garbage, so it renders wrong data on the
dashboard and would never fire a Discord restock alert. Concretely:

- **EB Games doesn't parse.** `JsonLdAdapter` reads the raw httpx HTML for
  `ebgames`, but EB Games product pages only expose their JSON-LD when
  browser-rendered. With no JSON-LD product node, `parse_stock_from_html` falls
  back to keyword markers, and these pages contain an "OutOfStock" string, so it
  returns a **false `out_of_stock` with an empty title** — a check that looks
  successful but is useless. This blocked 15 audited EB Games SKUs from being
  added.
- **Nothing surfaces a dead SKU.** Retailer-level health exists (a whole store
  blocking), but there is no product-level signal for "this one SKU has gone
  dead" (delisted, URL changed, page restructured).

## Goals

1. Make EB Games checks return real data, and add the 15 verified EB Games SKUs.
2. A one-time end-to-end audit confirming every product returns usable data.
3. An ongoing guard that alerts when any product stops returning usable data.

## Design

### 1. EB Games adapter fix (browser-first)

Add a `browser_first: bool = False` parameter to `JsonLdAdapter.__init__`. When
`True`, `check()` skips the httpx attempt and goes straight to
`_check_via_browser()` (headed real Chrome), which renders the page so the
JSON-LD product node is present, then parses it. Register `ebgames` with
`JsonLdAdapter(browser_first=True)` in the adapter registry. This mirrors how
Pokémon Center is browser-only, and is deterministic (no reliance on httpx
behavior that varies per EB Games page).

- Other `JsonLdAdapter` retailers are unaffected (default `browser_first=False`,
  existing httpx-then-fallback path unchanged).
- After the fix, add the 15 EB Games SKUs (booster box + ETB for Mega Evolution
  base, Phantasmal Flames, Perfect Order, Chaos Rising, Pitch Black, Journey
  Together, Surging Sparks; ETB-only for Ascended Heroes) to `watchlist.json`,
  each live-verified to parse real data (status + title + price) via the actual
  adapter before committing.

### 2. One-time end-to-end audit (extend `--check-once`)

`monitor.py --check-once` already runs every product once and prints per-product
status. Extend it to also print a **summary of products with NO USABLE DATA** —
where "no usable data" means `status is UNKNOWN` OR (`title` is empty/blank).
This gives an immediate post-deploy confirmation that all ~76 products return
real data, and names any that don't. No always-on code; just a clearer report.

### 3. Ongoing product-level health guard

Track, per product, a streak of consecutive "no usable data" checks and alert
when it crosses a threshold.

- **Signal — "usable data":** a `StockResult` is usable iff `status is not
  Status.UNKNOWN` AND `result.title` is a non-empty, non-whitespace string. This
  catches both the UNKNOWN case and the false-`out_of_stock`-with-empty-title
  case. A pure helper `is_usable(result) -> bool`.
- **Storage:** a new in-memory `ProductHealth` dataclass, tracked in a
  `defaultdict(ProductHealth)` keyed by `product.key` in `run()` — exactly
  mirroring the existing `health = defaultdict(RetailerHealth)`. NOT persisted:
  like `RetailerHealth`, it resets on restart, which is fine (a genuinely dead
  SKU re-trips within the threshold after a restart). This deliberately avoids
  `ProductState`/`state.json`, because `decide()` reconstructs `ProductState`
  every check and would wipe any streak stored there.
- **Logic:** `ProductHealth.record(result, threshold) -> str | None` —
  - if `is_usable(result)`: capture `was_stuck = self.warned_stuck`, reset
    `unusable_streak = 0` and `warned_stuck = False`, return `"recovered"` if
    `was_stuck` else `None`.
  - else: `unusable_streak += 1`; if it just reached `threshold` and not already
    warned, set `warned_stuck = True` and return `"stuck"`; else `None`.
  Threshold default **5**, read from config key `product_health_threshold`.
- **Wiring:** `process_product` takes the product's `ProductHealth` entry
  (passed like the `RetailerHealth` one). After a successful adapter call, call
  `record(result, threshold)` and on `"stuck"`/`"recovered"` send a Discord
  `build_system_embed` (e.g. *"⚠️ {retailer} {name} has returned no usable data
  for {n} checks — possibly delisted or the page changed"* / *"{retailer} {name}
  is returning data again"*). A `Blocked`/error path does NOT reach this code
  (it returns early), so blocks/errors never touch the product-health streak —
  they're covered by retailer-level health.

This is product-level health, complementing the existing retailer-level
`RetailerHealth` (blocking/errors) — separate concerns, separate state.

## Testing

- Unit tests (pure): `is_usable` (UNKNOWN → False; empty/whitespace title →
  False; real status + title → True). `ProductHealth.record` — usable resets
  streak; unusable increments; `"stuck"` fires once at threshold; `"recovered"`
  fires once on the clean read after being stuck; empty-title `out_of_stock`
  counts as unusable; threshold honored.
- Adapter test: `JsonLdAdapter(browser_first=True)` calls the browser path and
  skips httpx; default stays httpx-first.
- Live verification: a sample of the new EB Games SKUs parse real
  status/title/price through the real adapter.
- Manual: `--check-once` summary flags nothing after the EB Games fix.

## Where things live

- Committed (PR): `adapters/jsonld.py` (browser_first), adapter registry,
  `monitor.py` (`ProductHealth` + `is_usable` + wiring + `--check-once`
  summary), `watchlist.json` (15 EB Games SKUs), tests. (`ProductHealth` lives
  in `monitor.py` next to `RetailerHealth`; `state.py` is untouched.)
- Per-deployment (`pmonitor` `config.json`, optional): `product_health_threshold`
  if the default of 5 needs tuning.

## Non-goals

- No change to the retailer-level `RetailerHealth` / block alerts.
- No change to the marker fallback for other retailers (only EB Games is forced
  browser-first).
- No new retailers beyond EB Games (already sourced).
- No auto-buy.
