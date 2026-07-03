from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Iterator

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
OOS_MARKERS = ("out of stock", "sold out", "currently unavailable")
ATC_MARKERS = ("add to cart", "add to bag")


def parse_stock_from_html(html: str, url: str = "") -> StockResult:
    for match in LDJSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for node in _product_nodes(data):
            result = _result_from_product_node(node, url)
            if result is not None:
                return result
    return _fallback_from_markers(html, url)


def _product_nodes(data: object) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _product_nodes(item)
    elif isinstance(data, dict):
        if data.get("@type") == "Product":
            yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from _product_nodes(value)


def _result_from_product_node(node: dict, url: str) -> StockResult | None:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None
    availability = str(offers.get("availability", "")).lower()
    if "instock" in availability:
        status = Status.IN_STOCK
    elif "outofstock" in availability or "soldout" in availability:
        status = Status.OUT_OF_STOCK
    else:
        return None
    price: Decimal | None = None
    raw_price = offers.get("price")
    if raw_price is not None:
        try:
            price = Decimal(str(raw_price))
        except InvalidOperation:
            price = None
    return StockResult(status=status, price=price, title=node.get("name", ""), url=url)


def _fallback_from_markers(html: str, url: str) -> StockResult:
    lowered = html.lower()
    if any(m in lowered for m in OOS_MARKERS):
        return StockResult(status=Status.OUT_OF_STOCK, url=url)
    if any(m in lowered for m in ATC_MARKERS):
        return StockResult(status=Status.IN_STOCK, url=url)  # price unknown
    return StockResult(status=Status.UNKNOWN, url=url)


class JsonLdAdapter:
    """Generic adapter for retailers whose product pages carry schema.org JSON-LD."""

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(product.url)
        raise_if_blocked(response)
        response.raise_for_status()
        return parse_stock_from_html(response.text, product.url)
