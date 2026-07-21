# Pokemon Card Restock Monitor

Watches Canadian retailers (Best Buy, Walmart, Toys"R"Us, Indigo, EB Games,
Costco) and posts a Discord alert when a watched product is in stock at or
below your max price (CAD). You buy manually — this tool never auto-purchases.

## Adding / removing products

Edit `watchlist.json` — the monitor hot-reloads it, no restart needed:

    {
      "name": "Prismatic Evolutions ETB",
      "retailer": "bestbuy",        // bestbuy | walmart | toysrus | indigo |
                                     // ebgames | costco
      "sku": "17095567",            // required for bestbuy, optional otherwise
      "url": "https://www.bestbuy.ca/en-ca/product/17095567",
      "max_price": 64.99,
      "packs": 9,
      "set": "Prismatic Evolutions"
    }

## Web dashboard

The monitor publishes a live web page listing every watched product with its
current price, all-time-low price, cost per pack, stock status, and a link out —
grouped by set. It updates whenever a price or stock status changes (and once a
day on the heartbeat).

**One-time setup:**

    bash scripts/setup-pages.sh

This creates a `gh-pages` branch, a worktree at `~/.pokemon-monitor/pages`, and
enables GitHub Pages. The page appears at
`https://jackperejuan2.github.io/pokemon_monitor/`.

**How it works:** after each check the monitor updates `dashboard_data.json`
(local, gitignored) and, when something changed, renders a self-contained
`index.html` and force-pushes it to the `gh-pages` branch (a single rolling
commit — `main` history is never touched). GitHub Pages serves it. Publishing
failures are logged and never interrupt checks or alerts. The page is public and
contains only product display data — no secrets (the Discord webhook stays in
gitignored `config.json`).

Each watchlist entry needs `"packs"` (pack count, for cost-per-pack) and `"set"`
(for grouping) — see existing entries for the format.

## Browser-fallback retailers

Most retailers are checked with plain HTTP requests. A few need a real
browser:

- **walmart** and **ebgames** try plain HTTP first, and automatically retry
  through a real Chromium window if the plain request gets blocked (bot
  detection, challenge page, etc). Any JSON-LD retailer (toysrus, indigo,
  costco) that starts getting blocked will fall back the same way.

Because a real browser check is much slower than an HTTP request, these
retailers default to a longer check interval via `interval_overrides` in
`config.json` (600–900s, vs. 120–300s for plain-HTTP retailers). You'll see a
Chromium window pop up periodically for these — that's expected, not a bug.

## Browser retailers require a VISIBLE Chrome window

Walmart and EB Games block plain HTTP and headless browsers
(PerimeterX / Imperva). The only mode that gets through is **real Google Chrome
in a headed (visible) window** — the monitor launches `channel="chrome"`,
`headless=False` for these retailers. Empirically confirmed 2026-07-04:
new-headless real Chrome is blocked by both walls; only a visible window works.

Consequences:
- Each Walmart / EB Games check opens a Chrome window for
  ~20 seconds, then closes it. Best Buy uses a fast JSON API and never opens a
  window.
- These retailers are throttled to 30–60 min between checks
  (`interval_overrides` in `config.json`) to keep windows occasional.
- The monitor needs a real logged-in macOS session with a display — it cannot
  run "truly headless" on a server for these retailers. For windowless 24/7
  operation, run it in a separate macOS user account (fast-user-switch away) or
  on a spare Mac.
- Google Chrome must be installed (`/Applications/Google Chrome.app`).

No CAPTCHA solving or fingerprint spoofing is built in, by design.

## Known issue: launchd cannot start the monitor yet (macOS Documents protection)

The project lives under `~/Documents`, which macOS TCC-protects. Processes
spawned by launchd don't inherit Terminal's Documents-folder access, so the
service exits immediately with `PermissionError: ... pyvenv.cfg` and never
starts. The plist is installed at `~/Library/LaunchAgents/com.pokemonmonitor.plist`
but left **unloaded** until you do ONE of the following:

**Option A (recommended, no security changes): move the project out of Documents.**

    launchctl unload ~/Library/LaunchAgents/com.pokemonmonitor.plist 2>/dev/null
    mv ~/Documents/"Pokemon Monitor" ~/pokemon-monitor
    cd ~/pokemon-monitor
    rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    sed -i '' 's|/Users/northandunder/Documents/Pokemon Monitor|/Users/northandunder/pokemon-monitor|g' launchd/com.pokemonmonitor.plist
    cp launchd/com.pokemonmonitor.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.pokemonmonitor.plist

**Option B: grant Full Disk Access to the Python binary.** System Settings →
Privacy & Security → Full Disk Access → `+` → add
`/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9`,
then `launchctl load ~/Library/LaunchAgents/com.pokemonmonitor.plist`.

Either way, verify with `launchctl list | grep pokemonmonitor` (a real PID, not
`-`) and `tail -5 logs/monitor.log`, and expect a "Monitor started." message in
Discord.

Until then you can always run it manually from a terminal:
`.venv/bin/python monitor.py` (leave the window open).

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
- Walmart / EB Games checks open a real Chromium window briefly (~every
  30–60 min); this is required to get past their bot protection and is
  best-effort.
- Your Mac must be awake for checks to run: System Settings → prevent sleep,
  or run `caffeinate -s` — otherwise expect gaps.
- **Prices right now are scarcity-inflated.** Popular sets are frequently
  listed well above MSRP by resellers/retailers riding demand. The monitor
  only ever fires a green "buy now" alert when the price is at or under the
  `max_price` you set (your real MSRP target), so long quiet stretches with
  no alerts are normal and expected — it means nothing has hit your price
  yet, not that the monitor is broken. Check `logs/monitor.log` or the daily
  heartbeat if you want confirmation it's still alive.

## Development

    .venv/bin/pytest -v
