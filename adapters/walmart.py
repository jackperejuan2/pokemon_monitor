from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def parse_next_data(html: str, url: str = "") -> StockResult:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return StockResult(status=Status.UNKNOWN, url=url)
    try:
        data = json.loads(match.group(1))
        product = data["props"]["pageProps"]["initialData"]["data"]["product"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return StockResult(status=Status.UNKNOWN, url=url)
    if not isinstance(product, dict):
        return StockResult(status=Status.UNKNOWN, url=url)

    availability = str(product.get("availabilityStatus", "")).upper()
    if availability == "IN_STOCK":
        status = Status.IN_STOCK
    elif availability:
        status = Status.OUT_OF_STOCK
    else:
        status = Status.UNKNOWN

    price: Decimal | None = None
    price_info = product.get("priceInfo")
    raw = price_info.get("currentPrice") if isinstance(price_info, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("price") is not None:
        try:
            price = Decimal(str(raw["price"]))
        except InvalidOperation:
            price = None
    if price is not None and not price.is_finite():
        price = None
    return StockResult(status=status, price=price, title=product.get("name", ""), url=url)


class WalmartAdapter:
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(product.url)
        raise_if_blocked(response)
        response.raise_for_status()
        return parse_next_data(response.text, product.url)
