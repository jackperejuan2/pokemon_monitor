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

## Browser-fallback retailers

Most retailers are checked with plain HTTP requests. A few need a real
browser:

- **pokemoncenter** always loads the page in a real (headed) Chromium window,
  since Pokemon Center blocks plain HTTP entirely.
- **walmart** and **ebgames** try plain HTTP first, and automatically retry
  through a real Chromium window if the plain request gets blocked (bot
  detection, challenge page, etc). Any JSON-LD retailer (toysrus, indigo,
  costco) that starts getting blocked will fall back the same way.

Because a real browser check is much slower than an HTTP request, these three
retailers default to a longer check interval via `interval_overrides` in
`config.json` (600–900s, vs. 120–300s for plain-HTTP retailers). You'll see a
Chromium window pop up periodically for these — that's expected, not a bug.

## Known issue: Pokemon Center is currently hard-blocked

As of this writing, Pokemon Center serves an Imperva/Incapsula "incident"
challenge page to the monitor's browser profile on every check, so it will
show `ERROR: pokemoncenter served a challenge page` and never report real
stock status. Imperva tracks trust per browser profile, and the monitor's
profile hasn't built up any "looks human" history yet.

**Fix:** clear the block by browsing pokemoncenter.com manually, once, using
the *same* browser profile the monitor uses (`~/.pokemon-monitor/pc-profile`).

1. Quit the monitor first if it's running, so nothing else is using that
   profile at the same time:

       launchctl unload ~/Library/LaunchAgents/com.pokemonmonitor.plist

2. Open a real Chromium window on that profile and browse around normally
   (view the product page, scroll, maybe check another page) until the
   incident page stops appearing:

       .venv/bin/python -m playwright open --user-data-dir ~/.pokemon-monitor/pc-profile https://www.pokemoncenter.com/en-ca

3. Close that window once pages load normally, then restart the monitor:

       launchctl load ~/Library/LaunchAgents/com.pokemonmonitor.plist

If Pokemon Center starts hard-blocking again later, repeat the same steps.
This is a one-off manual unblock, not a permanent bypass — no CAPTCHA solving
or bot-evasion arms race is built into the monitor by design.

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
- **Prices right now are scarcity-inflated.** Popular sets are frequently
  listed well above MSRP by resellers/retailers riding demand. The monitor
  only ever fires a green "buy now" alert when the price is at or under the
  `max_price` you set (your real MSRP target), so long quiet stretches with
  no alerts are normal and expected — it means nothing has hit your price
  yet, not that the monitor is broken. Check `logs/monitor.log` or the daily
  heartbeat if you want confirmation it's still alive.

## Development

    .venv/bin/pytest -v
