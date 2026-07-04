"""Alert state machine and persistence for the restock monitor.

Invariant: decide() must emit at most one alert per price-OK transition;
UNKNOWN results never mutate known state.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from adapters.base import Product, Status, StockResult

log = logging.getLogger("state")

STATE_PATH = Path(__file__).parent / "state.json"
OVER_PRICE_COOLDOWN = timedelta(hours=24)


@dataclass
class ProductState:
    status: str = "unknown"
    price_ok: bool = False
    last_over_price_alert: str | None = None


@dataclass
class Decision:
    new_state: ProductState
    alert: str | None = None  # "restock" | "over_price" | None


def decide(prev: ProductState, result: StockResult, product: Product, now: datetime) -> Decision:
    if result.status is Status.UNKNOWN:
        # Transient parse/availability failure: keep what we knew, stay quiet.
        return Decision(ProductState(**asdict(prev)), None)

    if result.status is Status.IN_STOCK and result.price is None and prev.price_ok:
        # Price-scrape flake while we already knew the price was fine:
        # keep price_ok, update status, and stay quiet.
        new = ProductState(
            status=result.status.value,
            price_ok=True,
            last_over_price_alert=prev.last_over_price_alert,
        )
        return Decision(new, None)

    price_ok = (
        result.status is Status.IN_STOCK
        and result.price is not None
        and result.price <= product.max_price
    )
    new = ProductState(
        status=result.status.value,
        price_ok=price_ok,
        last_over_price_alert=prev.last_over_price_alert,
    )

    if price_ok and not prev.price_ok:
        return Decision(new, "restock")
    if result.status is Status.IN_STOCK and not price_ok and _over_price_due(prev.last_over_price_alert, now):
        new.last_over_price_alert = now.isoformat()
        return Decision(new, "over_price")
    return Decision(new, None)


def _over_price_due(last_iso: str | None, now: datetime) -> bool:
    if last_iso is None:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        # Corrupt timestamp: fail open — alert rather than go silent.
        return True
    return now - last >= OVER_PRICE_COOLDOWN


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
