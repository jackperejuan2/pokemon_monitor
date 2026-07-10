from __future__ import annotations

import asyncio
import logging

import httpx

from adapters.base import Product, StockResult

log = logging.getLogger("notifier")

COLOR_RESTOCK = 0x2ECC71      # green
COLOR_SYSTEM = 0xE74C3C       # red
COLOR_INFO = 0x95A5A6         # grey
COLOR_PRICE_DROP = 0x1ABC9C   # teal


def build_restock_embed(product: Product, result: StockResult) -> dict:
    return {
        "title": f"🟢 RESTOCK: {product.name}",
        "url": product.url,
        "color": COLOR_RESTOCK,
        "description": (
            f"**${result.price:.2f} CAD** (max ${product.max_price:.2f}) at **{product.retailer}**\n"
            f"[Buy now]({product.url})"
        ),
    }


def build_price_drop_embed(product: Product, result: StockResult, prev_lowest) -> dict:
    pct = (prev_lowest - result.price) / prev_lowest * 100
    per_pack = (f" · ${result.price / product.packs:.2f}/pack"
                if product.packs and product.packs > 0 else "")
    return {
        "title": f"📉 Price drop: {product.name}",
        "url": product.url,
        "color": COLOR_PRICE_DROP,
        "description": (
            f"**${result.price:.2f} CAD** (was ${prev_lowest:.2f}, −{pct:.0f}%)"
            f"{per_pack} at **{product.retailer}**\n"
            f"[Buy now]({product.url})"
        ),
    }


def build_system_embed(message: str) -> dict:
    return {"title": "⚠️ Monitor notice", "color": COLOR_SYSTEM, "description": message}


def build_heartbeat_embed(product_count: int, unhealthy: list[str]) -> dict:
    if unhealthy:
        detail = "degraded adapters: " + ", ".join(unhealthy)
    else:
        detail = "all adapters healthy"
    return {
        "title": "✅ Daily heartbeat",
        "color": COLOR_INFO,
        "description": f"Monitoring {product_count} products, {detail}.",
    }


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        value = response.json().get("retry_after")
        if value is not None:
            return float(value)
    except (ValueError, TypeError, AttributeError):
        pass
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return float(header)
        except ValueError:
            pass
    return 1.0


class Notifier:
    def __init__(self, webhook_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.webhook_url = webhook_url
        self._transport = transport
        # One reusable client for the process lifetime instead of a fresh client
        # (and connection) per send. Created lazily so it binds the event loop
        # that first calls send(). Bounded pool: this only ever talks to Discord.
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15,
                transport=self._transport,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def send(self, embed: dict) -> None:
        client = self._get_client()
        last_error = ""
        for attempt in range(1, 4):
            try:
                response = await client.post(self.webhook_url, json={"embeds": [embed]})
            except httpx.HTTPError as exc:
                last_error = repr(exc)
                delay = 1.0
            else:
                if response.is_success:
                    return
                last_error = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    delay = _retry_after_seconds(response)
                else:
                    delay = 1.0
            if attempt < 3:
                await asyncio.sleep(min(delay, 10))
        log.warning(
            "Discord webhook send failed after 3 attempts: %s", last_error
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
