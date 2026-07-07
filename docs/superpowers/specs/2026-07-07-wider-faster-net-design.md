# Wider, Faster Net — closing restock-miss gaps

**Date:** 2026-07-07
**Status:** Approved design, pending implementation
**Depends on:** the browser/socket-lifecycle hardening (PR #11) — must be merged and
deployed first, because this work increases check frequency and that hardening is
the guardrail that keeps higher frequency from re-triggering ephemeral-port
exhaustion.

## Problem

The monitor missed real restocks that the community (PokeCanada Discord) caught —
e.g. Pitch Black at Pokémon Center and Prismatic at Walmart. Root causes:

1. **Coverage gaps.** Walmart has only 1 watched product (yet carries most sets);
   Prismatic was never watched at Walmart at all.
2. **Slow polling.** Best Buy is checked every 2–5 min despite being a cheap,
   invisible, reliable JSON API. Browser retailers (PC/Walmart/EB) are checked
   every 30–60 min while hot drops sell out in seconds.

We are notification-only (no auto-buy), and flash drops can sell out faster than
any poll — so the goal is to **materially improve odds**, not guarantee catches.

Directly reading PokeCanada is explicitly out of scope: our Discord integration is
a send-only webhook, reading a third-party server needs an admin-invited bot, and a
self-bot violates Discord ToS / risks the user's account.

## Key constraint: the serialized-browser ceiling

All browser checks share a single lock (`adapters/browser._browser_lock`) — one
Chrome at a time, ~20s each. With ~27 browser products a full sweep already takes
~9 min minimum regardless of configured interval. Therefore:

- **Broad speed comes from Best Buy** (plain JSON API, parallel-safe, no block risk).
- **Browser retailers rely on prioritization + short turbo windows**, not
  blanket interval cuts. Tightening their interval below the serialization floor
  buys nothing.

## What already exists (do NOT rebuild)

- **Per-retailer interval tuning.** `check_interval()` already reads
  `interval_overrides.<retailer>`, a `pokemoncenter_interval_seconds` tier, and a
  `check_interval_seconds` default. So "faster Best Buy" and "tighter browser
  intervals" are pure `config.json` edits.
- **Blocked alerting.** `process_product()` catches `Blocked`, and
  `RetailerHealth.record_blocked()` fires a one-per-episode Discord system alert
  and applies exponential backoff. Recovery resets on the next success.

## Scope of this change

### 1. Config tuning (`pmonitor`'s `config.json`, no code)
- `interval_overrides.bestbuy = [60, 90]` — near-real-time on our 23 Best Buy
  products at zero cost / no block risk.
- `interval_overrides.walmart = [900, 1200]`, `interval_overrides.ebgames =
  [900, 1200]`, and Pokémon Center to `[900, 1200]` — ~15–20 min, about the
  serialization floor once Walmart entries are added. Invisible in `pmonitor`,
  so the extra Chrome activity costs the user nothing.

### 2. Walmart coverage (watchlist, committed)
Add ~6–8 high-value Walmart entries (booster box / ETB / bundle) for the current
in-print sets Walmart carries — prioritize the hot Mega Evolution-block sets and
Pitch Black rather than all 9 sets (respecting the browser ceiling). Uses the
existing `walmart` adapter unchanged — the work is sourcing real walmart.ca
product URLs and live-verifying each returns the correct product page unblocked.
Each entry carries `set` and `packs` like every other watchlist item.

### 3. Drop-window turbo mode (code + config) — the main new work
A `drop_windows` list in `config.json`:

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

`check_interval()` gains a branch: if `now` is inside an active window **and** the
product matches, use the window's turbo interval, overriding the normal tier.
Outside any window, behavior is unchanged. **Match semantics:** a product matches a
window if it satisfies every key present in `match` (AND); supported keys are `set`
and `retailer`; at least one key is required (an empty/absent `match` is treated as
malformed and skipped). If multiple windows are active and match, the shortest
turbo interval wins.

Design details:
- **Pure function.** Given `(product, config, now)` → interval. Fully unit-testable
  in the existing TDD style; `now` is already available in the run loop and must be
  passed in (currently `check_interval` reads no clock other than `random`).
- **Backoff interaction.** During an active turbo window, use the turbo interval
  *as-is* (do not multiply by `health.backoff`) — during a known drop we want to
  keep trying even through intermittent blocks — but keep a hard floor (e.g. ≥30s)
  so we never truly hammer.
- **Browser safety.** Turbo narrows to the matched hot set (few SKUs), so browser
  effort stays concentrated and bounded even at a 60–120s cadence. Best Buy does
  the safe fast polling.
- **Malformed windows fail open**: a bad entry is logged and skipped (like the
  existing `_parse_interval_range` fallbacks), never crashing the loop.

### 4. Optional block-alert polish (code, small)
- Add a "recovered" system notice when a previously-blocked retailer succeeds
  again, so the user knows visibility is restored.
- Audit the browser adapters (`walmart`, `pokemoncenter`) to confirm a challenge
  page raises `Blocked` rather than returning a silent `UNKNOWN`/out-of-stock —
  otherwise a block can slip past the existing alert and we're blind without
  knowing. Fix any that don't.

## Where each piece lives
- **Committed (PR, `pmonitor` pulls):** Walmart watchlist entries, drop-window
  logic in `check_interval`, optional block-alert polish.
- **`pmonitor`'s `config.json` (per-deployment, hot-reloaded):** Best Buy interval,
  browser intervals, the `drop_windows` data. Editing there (not via PR) is the
  accepted tradeoff for occasional drop-window ops.

## Testing
- Pure-function unit tests for `check_interval` drop-window logic: inside/outside a
  window, `set` vs `retailer` matching, turbo-ignores-backoff with floor, malformed
  window skipped.
- Unit test for the "recovered" notice (fires once on block→success transition).
- Each new Walmart URL live-verified once (correct product page, not blocked).
- Full suite stays green.

## Non-goals
- No new retailers (Amazon / Costco / GameStop).
- No auto-buy.
- No Discord ingestion / self-bot.
- No change to the serialized-browser model (one Chrome at a time stays).
