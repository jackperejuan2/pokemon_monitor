from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

from .base import Blocked, Product, StockResult
from .jsonld import parse_stock_from_html

PROFILE_DIR = Path.home() / ".pokemon-monitor" / "pc-profile"
CHALLENGE_MARKERS = ("access denied", "_incapsula_", "pardon our interruption", "captcha")

# PROFILE_DIR is a shared Chromium user-data-dir guarded by a SingletonLock;
# concurrent launches against it hang/throw, so serialize browser sessions.
_BROWSER_LOCK = asyncio.Lock()


def is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


class PokemonCenterAdapter:
    """Loads the product page in a real (headed) Chromium with a persistent
    profile, then reuses the JSON-LD/marker parser on the rendered HTML."""

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        # `client` is unused: Pokemon Center blocks plain HTTP.
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        async with _BROWSER_LOCK:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    str(PROFILE_DIR),
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    locale="en-CA",
                )
                try:
                    page = await context.new_page()
                    await page.goto(product.url, wait_until="domcontentloaded", timeout=60_000)
                    await page.wait_for_timeout(5_000)  # let JS/anti-bot settle
                    html = await page.content()
                finally:
                    await context.close()
        if is_challenge_page(html):
            raise Blocked("pokemoncenter served a challenge page")
        return parse_stock_from_html(html, product.url)
