"""Pokemon card restock monitor.

Usage:
    python monitor.py                # run forever
    python monitor.py --check-once   # check every watchlist product once and print results
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from adapters import ADAPTERS
from adapters.base import DEFAULT_HEADERS, Blocked, Product
from notifier import (
    Notifier,
    build_heartbeat_embed,
    build_over_price_embed,
    build_restock_embed,
    build_system_embed,
)
from dashboard import DashboardRecord, load_records, render_html, save_records, update_record
from publisher import publish, should_publish
from state import ProductState, decide, load_state, save_state

BASE_DIR = Path(__file__).parent
WATCHLIST_PATH = BASE_DIR / "watchlist.json"
CONFIG_PATH = BASE_DIR / "config.json"

log = logging.getLogger("monitor")


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
            packs=int(entry.get("packs", 1)),
            set_name=entry.get("set", "Other"),
        )
        for entry in data["products"]
    ]


def safe_reload(loader, previous, what):
    try:
        return loader()
    except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        log.warning("could not reload %s (%s); keeping previous", what, exc)
        return previous


def in_quiet_hours(now: datetime, config: dict) -> bool:
    quiet = config.get("quiet_hours")
    if not quiet:
        return False
    try:
        start = dtime.fromisoformat(quiet["start"])
        end = dtime.fromisoformat(quiet["end"])
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("bad quiet_hours in config (%r): %s; treating as not quiet", quiet, exc)
        return False
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end  # wraps midnight


def _parse_interval_range(raw, label: str) -> tuple[float, float] | None:
    """Returns (low, high) if `raw` is a valid 2-element numeric range, else
    None (caller should fall through to the next config tier)."""
    try:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("expected a 2-element list")
        return float(raw[0]), float(raw[1])
    except (TypeError, ValueError, IndexError, KeyError):
        log.warning("bad %s in config (%r); falling back", label, raw)
        return None


def check_interval(product: Product, config: dict, health: RetailerHealth) -> float:
    low_high = None

    overrides = config.get("interval_overrides")
    if isinstance(overrides, dict) and product.retailer in overrides:
        low_high = _parse_interval_range(
            overrides[product.retailer], f"interval_overrides.{product.retailer}"
        )

    if low_high is None and product.retailer == "pokemoncenter":
        if "pokemoncenter_interval_seconds" in config:
            low_high = _parse_interval_range(
                config["pokemoncenter_interval_seconds"], "pokemoncenter_interval_seconds"
            )

    if low_high is None and "check_interval_seconds" in config:
        low_high = _parse_interval_range(
            config["check_interval_seconds"], "check_interval_seconds"
        )

    if low_high is None:
        low_high = (120.0, 300.0)

    low, high = low_high
    return random.uniform(low, high) * health.backoff


async def process_product(client, notifier, product, states, records, health) -> bool:
    h = health[product.retailer]
    try:
        result = await ADAPTERS[product.retailer].check(client, product)
    except Blocked as exc:
        log.warning("%s blocked: %s", product.retailer, exc)
        if h.record_blocked():
            await notifier.send(
                build_system_embed(f"**{product.retailer}** is blocking checks ({exc}). Backing off.")
            )
        return False
    except Exception as exc:
        log.exception("%s check failed for %s", product.retailer, product.name)
        if h.record_error():
            await notifier.send(
                build_system_embed(f"**{product.retailer}** has failed 5 checks in a row: {exc}")
            )
        return False

    h.record_success()
    now = datetime.now()
    prev = states.get(product.key, ProductState())
    decision = decide(prev, result, product, now)
    states[product.key] = decision.new_state
    try:
        save_state(states)
    except Exception:
        log.exception("could not persist state")

    new_rec, changed = update_record(records.get(product.key, DashboardRecord()), result, now)
    records[product.key] = new_rec
    if changed:
        try:
            save_records(records)
        except Exception:
            log.exception("could not persist dashboard records")

    log.info("%s %s -> %s price=%s alert=%s",
             product.retailer, product.name, result.status.value, result.price, decision.alert)
    if decision.alert == "restock":
        await notifier.send(build_restock_embed(product, result))
    elif decision.alert == "over_price":
        await notifier.send(build_over_price_embed(product, result))
    return changed


def unhealthy_retailers(health):
    return sorted(
        name for name, h in health.items()
        if h.warned_blocked or h.consecutive_errors >= 5
    )


async def run():
    config = load_config()
    notifier = Notifier(config["discord_webhook_url"])
    states = load_state()
    records = load_records()
    last_publish = None
    health = defaultdict(RetailerHealth)
    next_check = {}
    last_heartbeat_date = None
    products = []

    await notifier.send(build_system_embed("Monitor started."))

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS, follow_redirects=True, timeout=30
    ) as client:
        while True:
            config = safe_reload(load_config, config, "config.json")
            products = safe_reload(load_watchlist, products, "watchlist.json")
            now = datetime.now()

            is_heartbeat = False
            heartbeat_hour = config.get("heartbeat_hour", 9)
            if now.hour >= heartbeat_hour and last_heartbeat_date != now.date():
                last_heartbeat_date = now.date()
                is_heartbeat = True
                await notifier.send(
                    build_heartbeat_embed(len(products), unhealthy_retailers(health))
                )

            quiet = in_quiet_hours(now, config)
            dirty = False
            if not quiet:
                for product in products:
                    if now < next_check.get(product.key, now):
                        continue
                    try:
                        changed = await asyncio.wait_for(
                            process_product(client, notifier, product, states, records, health),
                            timeout=180,
                        )
                        dirty = dirty or bool(changed)
                    except asyncio.TimeoutError:
                        log.warning("check timed out for %s", product.key)
                    next_check[product.key] = datetime.now() + timedelta(
                        seconds=check_interval(product, config, health[product.retailer])
                    )
                    await asyncio.sleep(random.uniform(2, 8))  # spread checks out
                    now = datetime.now()

            now2 = datetime.now()
            minutes_since = 999.0 if last_publish is None else (now2 - last_publish).total_seconds() / 60.0
            if should_publish(dirty, is_heartbeat, minutes_since):
                try:
                    html = render_html(products, records, now2,
                                       healthy=not unhealthy_retailers(health))
                    if publish(html):
                        last_publish = now2
                except Exception:
                    log.exception("dashboard render/publish failed")

            await asyncio.sleep(60 if quiet else 5)


async def check_once():
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


def main():
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
