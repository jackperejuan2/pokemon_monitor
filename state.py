"""Alert state machine and persistence for the restock monitor.

Invariant: decide() must emit at most one alert per price-OK transition;
UNKNOWN results never mutate known state.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adapters.base import Product, Status, StockResult

log = logging.getLogger("state")

STATE_PATH = Path(__file__).parent / "state.json"


@dataclass
class ProductState:
    status: str = "unknown"
    price_ok: bool = False


@dataclass
class Decision:
    new_state: ProductState
    alert: str | None = None  # "restock" | None


def decide(prev: ProductState, result: StockResult, product: Product, now: datetime) -> Decision:
    # We alert only on green restocks (in stock at/under max). "In stock over
    # max" is noise and is intentionally silent — see monitor's alert dispatch.
    if result.status is Status.UNKNOWN:
        # Transient parse/availability failure: keep what we knew, stay quiet.
        return Decision(ProductState(**asdict(prev)), None)

    if result.status is Status.IN_STOCK and result.price is None and prev.price_ok:
        # Price-scrape flake while we already knew the price was fine:
        # keep price_ok, update status, and stay quiet.
        return Decision(ProductState(status=result.status.value, price_ok=True), None)

    price_ok = (
        result.status is Status.IN_STOCK
        and result.price is not None
        and result.price <= product.max_price
    )
    new = ProductState(status=result.status.value, price_ok=price_ok)

    if price_ok and not prev.price_ok:
        return Decision(new, "restock")
    return Decision(new, None)


def should_alert_price_drop(prev_lowest, result, product, min_pct, min_abs) -> bool:
    """True iff `result` is an in-stock, buyable (<= max_price) new all-time low
    that is at least `min_abs` AND `min_pct` below `prev_lowest`. `prev_lowest`
    is the previously recorded lowest price (Decimal) or None (no prior low ->
    never a drop; that first buyable sighting is handled by the restock alert)."""
    if result.status is not Status.IN_STOCK or result.price is None:
        return False
    if result.price > product.max_price:
        return False
    if prev_lowest is None or result.price >= prev_lowest:
        return False
    drop = prev_lowest - result.price
    return drop >= Decimal(str(min_abs)) and (drop / prev_lowest) >= Decimal(str(min_pct))


def load_state() -> dict[str, ProductState]:
    if not STATE_PATH.exists():
        return {}
    try:
        raw = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read state file %s: %s; starting fresh", STATE_PATH, exc)
        return {}
    return {
        key: ProductState(
            **{k: v for k, v in value.items() if k in ProductState.__dataclass_fields__}
        )
        for key, value in raw.items()
    }


def save_state(states: dict[str, ProductState]) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({key: asdict(value) for key, value in states.items()}, indent=2)
    )
    os.replace(tmp, STATE_PATH)
