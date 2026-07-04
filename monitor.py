"""Pokemon card restock monitor.

Usage:
    python monitor.py                # run forever
    python monitor.py --check-once   # check every watchlist product once and print results
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
