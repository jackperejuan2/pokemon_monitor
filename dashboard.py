# dashboard.py
"""Dashboard data model, rendering, and persistence for the restock monitor.

Kept separate from the alert path (state.py): a display bug here can never affect
notifications. update_record and render_html are pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from adapters.base import Status, StockResult


@dataclass
class DashboardRecord:
    last_price: str | None = None       # Decimal serialized as str, or None
    last_status: str = "unknown"
    lowest_price: str | None = None
    last_checked: str | None = None     # ISO timestamp
    last_changed: str | None = None     # ISO timestamp


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

    lowest = prev.lowest_price
    if result.price is not None:
        if lowest is None or result.price < Decimal(lowest):
            lowest = new_price

    changed = (new_price != prev.last_price) or (new_status != prev.last_status)
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
