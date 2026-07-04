# dashboard.py
"""Dashboard data model, rendering, and persistence for the restock monitor.

Kept separate from the alert path (state.py): a display bug here can never affect
notifications. update_record and render_html are pure.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adapters.base import Status, StockResult

log = logging.getLogger("dashboard")
DASHBOARD_PATH = Path(__file__).parent / "dashboard_data.json"


@dataclass
class DashboardRecord:
    last_price: str | None = None       # Decimal serialized as str, or None
    last_status: str = "unknown"
    lowest_price: str | None = None
    last_checked: str | None = None     # ISO timestamp
    last_changed: str | None = None     # ISO timestamp


def _price_changed(new_price: str | None, old_price: str | None) -> bool:
    if new_price is None or old_price is None:
        return new_price != old_price
    return Decimal(new_price) != Decimal(old_price)


def update_record(prev: DashboardRecord, result: StockResult, now: datetime):
    """Returns (new_record, changed). changed is True when the current price or
    status differs from prev. UNKNOWN results never overwrite known data."""
    now_iso = now.isoformat()

    if result.status is Status.UNKNOWN:
        return (
            DashboardRecord(
                last_price=prev.last_price,
                last_status=prev.last_status,
                lowest_price=prev.lowest_price,
                last_checked=now_iso,
                last_changed=prev.last_changed,
            ),
            False,
        )

    new_status = result.status.value
    new_price = None if result.price is None else format(result.price, "f")

    # Price-flake tolerance mirrors state.py: an IN_STOCK result whose price
    # scrapes as None must not blank a previously known price.
    if (
        result.status is Status.IN_STOCK
        and result.price is None
        and prev.last_price is not None
    ):
        new_price = prev.last_price

    lowest = prev.lowest_price
    if result.price is not None:
        if lowest is None or result.price < Decimal(lowest):
            lowest = new_price

    changed = _price_changed(new_price, prev.last_price) or (new_status != prev.last_status)
    return (
        DashboardRecord(
            last_price=new_price,
            last_status=new_status,
            lowest_price=lowest,
            last_checked=now_iso,
            last_changed=now_iso if changed else prev.last_changed,
        ),
        changed,
    )


def load_records() -> dict[str, DashboardRecord]:
    if not DASHBOARD_PATH.exists():
        return {}
    try:
        raw = json.loads(DASHBOARD_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read %s (%s); starting fresh", DASHBOARD_PATH, exc)
        return {}
    fields = DashboardRecord.__dataclass_fields__
    return {
        key: DashboardRecord(**{k: v for k, v in value.items() if k in fields})
        for key, value in raw.items()
    }


def save_records(records: dict[str, DashboardRecord]) -> None:
    tmp = DASHBOARD_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({k: asdict(v) for k, v in records.items()}, indent=2))
    os.replace(tmp, DASHBOARD_PATH)
