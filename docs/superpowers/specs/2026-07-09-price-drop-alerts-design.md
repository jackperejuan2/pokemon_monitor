# Price-Drop Alerts — design

**Date:** 2026-07-09
**Status:** Approved design, pending implementation

## Problem

The monitor only alerts on the *transition* into buyable — `decide()` fires
`"restock"` when `price_ok and not prev.price_ok`. Once a product is recorded as
buyable, it stays quiet on every later check, **even if the price keeps
dropping**. So when the Walmart Chaos Rising Booster Bundle fell to a new low
(~$42), the dashboard silently recorded it as `lowest_price` but nothing pinged
Discord, because it was already in the buyable state. Users want to hear about a
price *getting cheaper*, not only about it *becoming available*.

## Goal

Alert on a **new all-time-low, buyable price** — an in-stock product hitting a
new record low that is at/under its `max_price`, gated by a minimum-drop
threshold to avoid penny-by-penny noise. Complements (does not replace) the
existing restock alert.

## Design

### 1. Trigger — pure function in `state.py`

```
should_alert_price_drop(prev_lowest, result, product, min_pct, min_abs) -> bool
```

Returns `True` iff ALL hold:
- `result.status is Status.IN_STOCK` and `result.price is not None`
- **buyable:** `result.price <= product.max_price`
- **prior low exists:** `prev_lowest is not None` (so the first-ever sighting
  can't fire a drop — that path is the restock)
- **new all-time low:** `result.price < prev_lowest`
- **meaningful:** let `drop = prev_lowest - result.price`; require
  `drop >= min_abs` AND `drop / prev_lowest >= min_pct`.

`prev_lowest` is passed as a plain `Decimal | None` (decoupled from the dashboard
record) so the function is trivially unit-testable. Lives next to `decide()`.

### 2. Wiring — `process_product` in `monitor.py`

The dashboard already persists each product's `lowest_price`
(`DashboardRecord.lowest_price`, a stringified Decimal), so there is **no new
storage**. Capture the prior dashboard record *before* `update_record` runs, and
convert its `lowest_price` to `Decimal | None`. Then, where the alert is sent
today:

```
if decision.alert == "restock":
    await notifier.send(build_restock_embed(product, result))
elif should_alert_price_drop(prior_lowest, result, product, min_pct, min_abs):
    await notifier.send(build_price_drop_embed(product, result, prior_lowest))
```

The `elif` guarantees a restock at a new low sends exactly one alert (the
restock), and a drop *while already buyable* is what fires the drop alert — the
Walmart-bundle case. Thresholds come from config (see §4).

### 3. New embed — `build_price_drop_embed` in `notifier.py`

Distinct color from restock green (teal, `COLOR_PRICE_DROP = 0x1ABC9C`) so it
reads as a different event. Title `📉 Price drop: {product.name}`. Body shows the
new price, the previous low, the percent drop, `$/pack`
(`result.price / product.packs`), the retailer, and the buy link. Mirrors
`build_restock_embed`'s shape.

### 4. Config

Two keys, both parsed defensively (bad/missing value → default, mirroring
`_product_health_threshold`), so a typo can't crash the daemon:
- `price_drop_min_pct` — default **0.02** (2%)
- `price_drop_min_abs` — default **1.00** ($1)

Read from the hot-reloaded `config` in `run()` and passed into `process_product`
(like `health_threshold`). Helpers `_price_drop_min_pct(config)` /
`_price_drop_min_abs(config)` (or one helper returning both).

### 5. Data flow / dedup notes

- Uses the all-time `lowest_price`, which `update_record` maintains. After a drop
  alert, `update_record` lowers `lowest_price` to the new price, so the *next*
  new low is measured against it — the threshold prevents repeated small pings.
- A bounce back to a previously-seen (non-record) price does **not** alert
  (new-low-only, per the chosen trigger).
- Over-max drops do **not** alert (buyable gate). A first buyable price that is
  also a new low coincides with the restock transition and is covered by the
  restock alert (the `elif`).

## Testing

- Pure-function unit tests for `should_alert_price_drop`: new buyable low above
  threshold → True; not-a-new-low → False; over-max → False; OUT_OF_STOCK →
  False; `prev_lowest is None` → False; drop below `min_abs` → False; drop below
  `min_pct` → False.
- Defensive-parse tests for the two threshold helpers (good value, missing key,
  non-numeric → default).
- Integration test in `process_product`: a restock at a new low sends only the
  restock (drop suppressed by the `elif`); a subsequent in-stock check at a new
  low below threshold sends nothing; one above threshold sends the price-drop
  embed.
- `build_price_drop_embed` shape test (color, title contains "Price drop",
  description contains new price + previous low).

## Where things live

- Committed (PR): `state.py` (`should_alert_price_drop`), `notifier.py`
  (`build_price_drop_embed` + color), `monitor.py` (threshold helpers + wiring),
  tests.
- Per-deployment (`pmonitor` `config.json`, optional): `price_drop_min_pct` /
  `price_drop_min_abs` to override the 2% / $1 defaults.

## Non-goals

- No change to the restock state machine (`decide()` / `ProductState`).
- No alerts for drops that are still over `max_price`.
- No "any drop" alerts (new-all-time-low only).
- No new persistence — reuses the dashboard's `lowest_price`.
