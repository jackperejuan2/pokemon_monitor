from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from .base import Blocked, Product, Status, StockResult, raise_if_blocked
from .browser import fetch_page_html

NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

BLOCKED_MARKERS = (
    "/blocked",
    "px-captcha",
    "robot or human",
    "forbidden",
    "access denied",
)

PROFILE_NAME = "walmart-profile"


def looks_blocked(html_or_url: str) -> bool:
    """True if the given response body or URL looks like a PerimeterX block
    page (walmart.ca redirects to /blocked, or serves a px-captcha /
    "Robot or human" interstitial)."""
    lowered = html_or_url.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


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
        try:
            response = await client.get(product.url)
            raise_if_blocked(response)
            response.raise_for_status()
            if not looks_blocked(str(response.url)) and not looks_blocked(response.text):
                return parse_next_data(response.text, product.url)
        except Blocked:
            pass

        # httpx path was blocked (403/429, or a PerimeterX /blocked redirect,
        # or a px-captcha interstitial) -- fall back to a real browser.
        #
        # Empirically (see experiment matrix, 2026-07-04): real-Chrome
        # NEW-HEADLESS (channel="chrome", headless=True) is still detected and
        # blocked by PerimeterX (bare "Forbidden" body). Only a headed real
        # Chrome window passes. Bundled headless Chromium is blocked outright.
        # So this intentionally opens a visible Chrome window.
        fallback_html = await fetch_page_html(
            product.url, profile=PROFILE_NAME, headless=False, channel="chrome"
        )
        if looks_blocked(fallback_html):
            raise Blocked("walmart blocked both http and browser")
        return parse_next_data(fallback_html, product.url)
