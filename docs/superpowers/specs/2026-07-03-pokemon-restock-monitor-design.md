# Pokemon Card Restock Monitor — Design

**Date:** 2026-07-03
**Status:** Approved design, pending implementation plan

## Purpose

Monitor Canadian retailer websites for Pokemon card restocks and send an instant
Discord notification when a watched product comes back in stock at or below a
user-set maximum price (MSRP). The user completes the purchase manually — the
tool never adds to cart or checks out.

## Scope

**In scope (v1):**
- Seven Canadian retailers: BestBuy.ca, Walmart.ca, Toys"R"Us Canada, Indigo
  (chapters.indigo.ca), EB Games (gamestop.ca), Costco.ca, Pokemon Center
  Canada (pokemoncenter.com/en-ca)
- Specific-product watchlist (exact URLs/SKUs), all prices in CAD
- Discord webhook alerts
- Runs 24/7 on the user's Mac via `launchd`

**Out of scope:**
- Auto-cart / auto-checkout (ToS violations, ban risk)
- Keyword/search-based product discovery
- CAPTCHA solving or bot-evasion arms race — when blocked, back off and warn
- Proxies; the monitor uses the home residential IP only

## Architecture

Python 3.12. `httpx` for async HTTP, `playwright` for the browser-based
adapter, plain JSON files for config and state (no database).

```
pokemon-monitor/
├── watchlist.json          # products: retailer, URL/SKU, max price CAD
├── config.json             # Discord webhook, intervals, quiet hours
├── monitor.py              # main loop: scheduling, dedup, backoff
├── adapters/
│   ├── base.py             # adapter interface: check(product) -> StockResult
│   ├── bestbuy.py          # BestBuy.ca availability API (JSON)
│   ├── walmart.py          # Walmart.ca embedded __NEXT_DATA__ JSON
│   ├── toysrus.py          # product page: JSON-LD, HTML fallback
│   ├── indigo.py           # product page: JSON-LD, HTML fallback
│   ├── ebgames.py          # product page: JSON-LD, HTML fallback
│   ├── costco.py           # product page: JSON-LD, HTML fallback
│   └── pokemoncenter.py    # Playwright browser (hostile bot protection)
├── notifier.py             # Discord webhook, rich embeds
└── state.json              # last-known status per product
```

### Adapter contract

Every adapter implements one async function:

```
check(product) -> StockResult
StockResult = {status: IN_STOCK | OUT_OF_STOCK | UNKNOWN,
               price: Decimal | None, title: str, url: str}
```

The main loop is retailer-agnostic; adding a retailer means adding one file.

### Per-retailer detection strategy

| Retailer | Method |
|---|---|
| BestBuy.ca | JSON availability endpoint (`api/v2/json/availability`) by SKU + product endpoint for price. Most reliable. |
| Walmart.ca | Fetch product page, parse embedded `__NEXT_DATA__` JSON for availability/price. PerimeterX-protected: realistic headers, conservative intervals. |
| Toys"R"Us CA | Product page → schema.org JSON-LD; fallback to add-to-cart button presence. |
| Indigo | Same JSON-LD strategy. |
| EB Games | Same JSON-LD strategy. |
| Costco.ca | Same JSON-LD strategy. |
| Pokemon Center CA | Playwright Chromium with persistent profile, rendered-page stock read, ~10-minute interval. Best-effort in v1; most likely to need iteration. |

### Data flow (per cycle)

1. Main loop schedules each watchlist product at a randomized jittered
   interval (2–5 min; ~10 min for Pokemon Center).
2. The retailer's adapter returns a `StockResult`.
3. If the product transitioned OUT_OF_STOCK → IN_STOCK **and**
   `price <= max_price`, fire a Discord alert.
4. New state is persisted to `state.json` — exactly one alert per restock
   event; re-alert only after the product goes out of stock and returns.

## Anti-blocking rules

1. **Jittered scheduling** — randomized check moments, checks spread out
   across retailers, never bursts.
2. **Realistic identity** — Chrome-on-Mac headers; cookies persisted per
   retailer across checks.
3. **Exponential backoff** — 403/429/CAPTCHA doubles that retailer's interval
   (cap 1 hour) and posts a one-time Discord warning; recovery resets.
4. **Quiet hours** — optional configurable overnight pause.

Design stance: polite scraper, not bot evasion. If a retailer escalates, the
tool backs off and reports rather than fighting.

## Notifications (Discord webhook)

- **Restock alert:** rich embed — product image, title, price vs. max,
  retailer, direct product link. Fired only on the in-stock transition at or
  under max price.
- **Over-MSRP notice:** distinct, quieter embed when in stock but above max;
  rate-limited to once per day per product.
- **System messages:** adapter blocked, adapter crashed, monitor started —
  same webhook, visually distinct.
- **Daily heartbeat:** "monitoring N products, all adapters healthy" so
  silence is a meaningful failure signal.

## Configuration

`watchlist.json` (hot-reloaded; edits require no restart):

```json
{
  "products": [
    {
      "name": "Prismatic Evolutions ETB",
      "retailer": "bestbuy",
      "sku": "17095567",
      "url": "https://www.bestbuy.ca/en-ca/product/...",
      "max_price": 64.99
    }
  ]
}
```

`config.json`: Discord webhook URL, base interval bounds, quiet hours.

## Error handling

1. **Isolation:** one adapter failing never affects others; every check is
   exception-wrapped, logged, and counted.
2. **Escalation:** 5 consecutive failures on an adapter → one Discord
   warning, then silent retries with backoff.
3. **Supervision:** `launchd` keeps the process alive across crashes and
   reboots; daily heartbeat confirms liveness.

## Testing

- **Adapter unit tests** against saved real HTML/JSON fixtures (one in-stock
  and one out-of-stock capture per retailer) — parsing verified offline.
- **State-machine unit tests** for dedup, backoff, and alert-once logic.
- **Live smoke test:** `python monitor.py --check-once` runs every watchlist
  item once and prints results; used to validate adapters against real sites
  during development and to debug after retailer redesigns.
