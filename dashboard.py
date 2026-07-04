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
from decimal import Decimal, InvalidOperation
from html import escape
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


_PAGE_CSS = """
body{font-family:-apple-system,system-ui,sans-serif;background:#15171c;color:#e6e6e6;
  margin:0;padding:24px;max-width:1100px;margin:0 auto}
h1{font-size:20px} .meta{opacity:.65;font-size:13px;margin-bottom:22px}
.setblock{margin-bottom:24px;border:1px solid rgba(255,255,255,.12);border-radius:10px;overflow:hidden}
.sethead{padding:10px 14px;background:rgba(255,255,255,.06);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:7px 12px;font-size:11px;opacity:.55;text-transform:uppercase}
td{padding:9px 12px;border-top:1px solid rgba(255,255,255,.07)}
tr.buy td{background:rgba(46,204,113,.14)}
.pill{font-size:11px;padding:2px 7px;border-radius:20px}
.in{background:rgba(46,204,113,.22);color:#7ee2a8}.out{background:rgba(255,255,255,.09);opacity:.6}
.good{color:#7ee2a8;font-weight:600}.bad{color:#e8a0a0;font-weight:600}
.muted{opacity:.45}a{color:#6db3f2;text-decoration:none}
"""


def _per_pack(price: Decimal, packs: int) -> str:
    if packs <= 0:
        return '<span class="muted">&mdash;</span>'
    per = price / packs
    cls = "good" if per <= 10 else "bad"
    return f'<span class="{cls}">${per:.2f}</span>'


def _safe_decimal(value: str | None) -> Decimal | None:
    """Parse a persisted price string, treating corrupt/hand-edited values as
    missing so a bad record can never make render_html raise."""
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _row(product, rec: DashboardRecord) -> str:
    status = rec.last_status
    pill = ('<span class="pill in">in stock</span>' if status == "in_stock"
            else f'<span class="pill out">{escape(status.replace("_", " "))}</span>')
    price = _safe_decimal(rec.last_price)
    is_buy = price is not None and price <= product.max_price
    cur = f"${price:.2f}" if price is not None else '<span class="muted">&mdash;</span>'
    perpack = _per_pack(price, product.packs) if price is not None else '<span class="muted">&mdash;</span>'
    lowest_val = _safe_decimal(rec.lowest_price)
    lowest = f"${lowest_val:.2f}" if lowest_val is not None else '<span class="muted">&mdash;</span>'
    tr = ' class="buy"' if is_buy else ""
    return (
        f"<tr{tr}><td>{escape(product.name)}</td>"
        f'<td class="muted">{escape(product.retailer)}</td>'
        f"<td>{pill}</td><td>{cur}</td><td>{perpack}</td>"
        f'<td class="muted">{lowest}</td>'
        f'<td><a href="{escape(product.url)}">open &#8599;</a></td></tr>'
    )


def render_html(products, records, now: datetime, healthy: bool) -> str:
    # Preserve first-seen set order from the watchlist.
    order = []
    grouped: dict[str, list] = {}
    for p in products:
        if p.set_name not in grouped:
            grouped[p.set_name] = []
            order.append(p.set_name)
        grouped[p.set_name].append(p)

    blocks = []
    for set_name in order:
        rows = "".join(_row(p, records.get(p.key, DashboardRecord())) for p in grouped[set_name])
        blocks.append(
            f'<div class="setblock"><div class="sethead">{escape(set_name)}</div>'
            "<table><tr><th>Variant</th><th>Retailer</th><th>Status</th><th>Current</th>"
            "<th>$/pack</th><th>Lowest ever</th><th></th></tr>"
            f"{rows}</table></div>"
        )

    health = "healthy &#9989;" if healthy else "degraded &#9888;"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Pokemon Restock Monitor</title>"
        f"<style>{_PAGE_CSS}</style></head><body>"
        "<h1>&#127918; Pokemon Restock Monitor</h1>"
        f'<div class="meta">{len(products)} products &middot; {len(order)} sets '
        f"&nbsp;|&nbsp; updated {escape(now.strftime('%Y-%m-%d %H:%M'))} &nbsp;|&nbsp; {health}</div>"
        f"{''.join(blocks)}</body></html>"
    )
