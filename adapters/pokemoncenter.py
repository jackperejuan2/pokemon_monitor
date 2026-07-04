from __future__ import annotations

import httpx

from .base import Blocked, Product, StockResult
from .browser import (
    CHALLENGE_MARKERS,
    _browser_lock,
    fetch_page_html_with_challenge_retry,
    is_challenge_page,
)
from .jsonld import parse_stock_from_html

# Re-exported for backwards compatibility: tests and any external callers
# import CHALLENGE_MARKERS/_browser_lock/is_challenge_page from this module.
__all__ = [
    "CHALLENGE_MARKERS",
    "PokemonCenterAdapter",
    "is_challenge_page",
]

PROFILE_NAME = "pc-profile"


class PokemonCenterAdapter:
    """Loads the product page in a real (headed) Chromium with a persistent
    profile, then reuses the JSON-LD/marker parser on the rendered HTML.

    Incapsula challenge pages typically auto-resolve via JS within ~5-15s, so
    we keep the page open and re-read the content a couple more times before
    giving up.
    """

    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        # `client` is unused: Pokemon Center blocks plain HTTP.
        html = await fetch_page_html_with_challenge_retry(
            product.url,
            profile=PROFILE_NAME,
            is_challenge=is_challenge_page,
            retries=2,
            retry_wait_ms=10_000,
            channel="chrome",
        )
        if is_challenge_page(html):
            raise Blocked("pokemoncenter served a challenge page")
        return parse_stock_from_html(html, product.url)
