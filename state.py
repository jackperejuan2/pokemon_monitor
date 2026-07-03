from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from adapters.base import Product, Status, StockResult

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
    return now - datetime.fromisoformat(last_iso) >= OVER_PRICE_COOLDOWN


def load_state() -> dict[str, ProductState]:
    if not STATE_PATH.exists():
        return {}
    raw = json.loads(STATE_PATH.read_text())
    return {key: ProductState(**value) for key, value in raw.items()}


def save_state(states: dict[str, ProductState]) -> None:
    STATE_PATH.write_text(
        json.dumps({key: asdict(value) for key, value in states.items()}, indent=2)
    )
