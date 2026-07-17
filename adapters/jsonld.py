from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Iterator

import httpx

from .base import Blocked, Product, Status, StockResult, raise_if_blocked
from .browser import fetch_page_html, is_challenge_page

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
        except (json.JSONDecodeError, RecursionError):
            # RecursionError: pathologically deep JSON; degrade to markers.
            continue
        for node in _product_nodes(data):
            result = _result_from_product_node(node, url)
            if result is not None:
                return result
    return _fallback_from_markers(html, url)


MAX_DEPTH = 20


def _is_product_type(value: object) -> bool:
    return value == "Product" or (isinstance(value, list) and "Product" in value)


def _product_nodes(data: object, depth: int = 0) -> Iterator[dict]:
    if depth > MAX_DEPTH:
        return
    if isinstance(data, list):
        for item in data:
            yield from _product_nodes(item, depth + 1)
    elif isinstance(data, dict):
        if _is_product_type(data.get("@type")):
            yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from _product_nodes(value, depth + 1)


def _result_from_product_node(node: dict, url: str) -> StockResult | None:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None
    availability = str(offers.get("availability", "")).lower()
    # PreOrder/PreSale are buyable now (reserve at the listed price), so treat
    # them like InStock: a priced pre-order should alert, not be dismissed.
    if "instock" in availability or "preorder" in availability or "presale" in availability:
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
    if price is not None and not price.is_finite():
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

    def __init__(self, browser_first: bool = False) -> None:
        self.browser_first = browser_first

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        if self.browser_first:
            # Some sites (EB Games) only expose their JSON-LD when JS-rendered;
            # httpx returns a product-node-less page that the marker fallback
            # misreads. Go straight to a real browser render for those.
            return await self._check_via_browser(product)
        try:
            response = await client.get(product.url)
            raise_if_blocked(response)
            response.raise_for_status()
        except Blocked:
            return await self._check_via_browser(product)
        return parse_stock_from_html(response.text, product.url)

    async def _check_via_browser(self, product: Product) -> StockResult:
        # Real-Chrome NEW-HEADLESS still gets blocked by PerimeterX-style bot
        # walls (see experiment matrix, 2026-07-04, for Walmart which shares
        # the same PerimeterX-family protection as EB Games). Use a headed
        # real Chrome window instead -- bundled headless Chromium is blocked
        # outright, and real-Chrome headless is also detected.
        fallback_html = await fetch_page_html(
            product.url, profile=f"{product.retailer}-profile", headless=False, channel="chrome"
        )
        if is_challenge_page(fallback_html):
            raise Blocked(f"{product.retailer} served a challenge page")
        return parse_stock_from_html(fallback_html, product.url)
