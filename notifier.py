from __future__ import annotations

import httpx

from adapters.base import Product, StockResult

COLOR_RESTOCK = 0x2ECC71      # green
COLOR_OVER_PRICE = 0xF1C40F   # yellow
COLOR_SYSTEM = 0xE74C3C       # red
COLOR_INFO = 0x95A5A6         # grey


def build_restock_embed(product: Product, result: StockResult) -> dict:
    return {
        "title": f"🟢 RESTOCK: {product.name}",
        "url": product.url,
        "color": COLOR_RESTOCK,
        "description": (
            f"**${result.price} CAD** (max ${product.max_price}) at **{product.retailer}**\n"
            f"[Buy now]({product.url})"
        ),
    }


def build_over_price_embed(product: Product, result: StockResult) -> dict:
    price_text = f"${result.price} CAD" if result.price is not None else "price unknown"
    return {
        "title": f"🟡 In stock over max: {product.name}",
        "url": product.url,
        "color": COLOR_OVER_PRICE,
        "description": f"{price_text} (max ${product.max_price}) at {product.retailer}",
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


class Notifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, embed: dict) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(self.webhook_url, json={"embeds": [embed]})
            response.raise_for_status()
