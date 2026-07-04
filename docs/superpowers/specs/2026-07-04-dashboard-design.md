# Restock Monitor Dashboard — Design

**Date:** 2026-07-04
**Status:** Approved design, pending implementation plan

## Purpose

A public web page that shows, for every watched product: current price, all-time
lowest price observed, cost per pack, in/out-of-stock status, and a link to the
product. The page is regenerated and published by the local monitor whenever a
price or stock status changes, and served via GitHub Pages.

## Scope

**In scope:**
- A grouped-by-set static HTML dashboard (one section per set; rows per variant).
- Tracking current price, current status, and all-time lowest observed price per
  product.
- Cost-per-pack computed from an explicit per-product pack count.
- Publishing the page to a `gh-pages` branch (rolling single commit) served by
  GitHub Pages.
- One-time setup script to create the branch/worktree and enable Pages.

**Out of scope:**
- Running checks in CI (GitHub Actions cannot reach retailers from datacenter IPs
  — the local monitor remains the only thing that performs checks).
- Price history/graphs beyond the single all-time-low figure.
- Any interactivity requiring a server (the page is static).
- Authentication / private hosting (the page is intentionally public; it contains
  only non-sensitive product display data).

## Decisions (from brainstorming)

- **"Lowest price" = lowest ever observed** for a product, regardless of stock or
  whether it beat the max. Shows how low the price has actually dropped.
- **Hosting = GitHub Pages, Mac pushes the data.** The monitor renders and
  publishes; Pages serves. No Actions build step.
- **Publish cadence = only when something changes**, plus once on the daily
  heartbeat so the page proves liveness at least daily.
- **Layout = grouped by set**, columns: Variant · Retailer · Status · Current ·
  $/pack · Lowest ever · link. Buy-worthy rows (current ≤ max) highlighted green.
- **Page is public** (world-readable). Accepted by the user.

## Architecture

Three concerns, kept separate from the alert path so notification behavior is
untouched.

```
dashboard.py    # DashboardRecord, update_record(), render_html(), load/save_records
publisher.py    # publish(html) -> gh-pages worktree, force-push rolling commit
monitor.py      # (modified) wire dashboard update + publish into the loop;
                #             add `packs` to Product and load_watchlist()
watchlist.json  # (modified) add "packs" to all entries
scripts/setup-pages.sh   # one-time: orphan gh-pages branch, worktree, enable Pages
```

### dashboard.py

```
@dataclass
class DashboardRecord:
    last_price: str | None = None      # Decimal serialized as str, or None
    last_status: str = "unknown"       # in_stock | out_of_stock | unknown
    lowest_price: str | None = None    # all-time low observed
    last_checked: str | None = None    # ISO timestamp of most recent check
    last_changed: str | None = None    # ISO timestamp of last price/status change
```

- `update_record(prev: DashboardRecord, result: StockResult, now: datetime)
   -> tuple[DashboardRecord, bool]` — pure. Returns the new record and whether a
  **meaningful change** occurred (price differs or status differs from previous).
  Rules:
  - `UNKNOWN` result: keep prev price/status/lowest (fail-safe, mirrors
    `state.decide`); update `last_checked`; `changed = False`.
  - Otherwise: set `last_status` from result; set `last_price` from result price
    (may be None if in stock but price unparsed); if result price is not None and
    (`lowest_price` is None or price < lowest), update `lowest_price`; set
    `last_checked = now`; if price or status differs from prev, set
    `last_changed = now` and `changed = True`.
- `load_records()/save_records()` — JSON at `DASHBOARD_PATH` (gitignored),
  tolerant load (missing file → {}, corrupt → {} + warn, unknown keys filtered),
  atomic temp-file + os.replace write. Same discipline as `state.py`.
- `render_html(products: list[Product], records: dict[str, DashboardRecord],
   now: datetime, healthy: bool) -> str` — pure. Produces a self-contained HTML
  document (inline CSS, no external assets): a header with product/set counts,
  "data last changed" and "last checked" times, and a health indicator; then one
  section per set (grouping by a `set_name` derived from the product), each a
  table with the approved columns. `$/pack = last_price / product.packs` (blank
  when price is None or packs is 0). Buy-worthy row when
  `last_price is not None and Decimal(last_price) <= product.max_price`.

**Set grouping:** products are grouped by an explicit `set_name`, read from a new
`"set"` field on each watchlist entry (deriving the set from the product name
would be brittle). This keeps `render_html` a pure function of its inputs. Set
sections render in first-seen watchlist order.

### publisher.py

- `PAGES_WORKTREE = ~/.pokemon-monitor/pages` (outside `~/Documents`).
- `publish(html: str) -> bool` — writes `index.html` into the worktree,
  `git -C <worktree> add -A`, `git commit --amend --no-edit` (rolling single
  commit; falls back to a fresh commit if none exists), `git push --force origin
  gh-pages`. Returns success; **never raises** — all git/network errors are
  caught and logged, so a publish failure never disrupts checks or alerts.
- A `should_publish(dirty: bool, is_heartbeat: bool) -> bool` pure helper
  encodes the cadence rule (publish when dirty, or on the daily heartbeat).

### monitor.py changes

- `Product` gains `packs: int` and `set_name: str` (from watchlist `"packs"` /
  `"set"`); `load_watchlist()` reads them.
- In `process_product`, after the existing alert logic: update the product's
  dashboard record, save records, and set a loop-level `dirty` flag if changed.
- In `run()`, after each product pass: if `should_publish(dirty, heartbeat)`,
  render the page from the full watchlist + records and call `publish()`; reset
  `dirty`. The daily-heartbeat branch also triggers a publish.

### watchlist.json changes

Add `"packs"` and `"set"` to all 45 entries. Pack counts are already known
(box 36, standard ETB 9, PC ETB 11, bundle 6, single 1, 2-blister 2, 3-blister 3).

## Data flow (per cycle)

1. Adapter returns `StockResult`.
2. Alert path runs unchanged (`decide` → `save_state` → maybe notify).
3. `update_record` refreshes the product's dashboard record (current, lowest,
   timestamps) and reports whether it changed; records saved.
4. After the pass, if anything changed (or daily heartbeat): `render_html` over
   all products+records, then `publish` to `gh-pages`.

## Hosting / setup

`scripts/setup-pages.sh` (run once): create an orphan `gh-pages` branch with a
seed `index.html`, add the worktree at `~/.pokemon-monitor/pages`, and enable
GitHub Pages on the `gh-pages` branch via `gh api`. The published page URL is
`https://<user>.github.io/<repo>/`. Documented in the README.

## Error handling

- Publish failures (git or network) are caught, logged, and swallowed —
  identical fail-open posture to `Notifier.send` and `save_state`. A broken
  publish never affects monitoring or alerts.
- `UNKNOWN` results never overwrite a known price or status.
- Corrupt `dashboard_data.json` degrades to an empty record set with a warning
  rather than crashing.

## Testing

- **Unit (pure):** `update_record` (lowest tracking, change detection, in/out,
  unknown passthrough, first-observation), `$/pack` math (including
  price-None/packs-0), `render_html` (grouping by set, buy-row highlighting,
  missing-price rows, counts/timestamps), `should_publish` cadence logic.
- **Persistence:** `dashboard_data.json` round-trip, missing-file, corrupt-file,
  unknown-key tolerance.
- **Publish:** the git-worktree mechanics are validated with a one-time live
  smoke test (set up worktree, publish sample HTML, confirm the `gh-pages`
  branch updated and the Pages URL serves it) rather than mocked in unit tests.
