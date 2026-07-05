import asyncio
import json
from decimal import Decimal

import httpx

from adapters.base import Product, Status, StockResult
from notifier import (
    COLOR_INFO,
    COLOR_RESTOCK,
    COLOR_SYSTEM,
    Notifier,
    build_heartbeat_embed,
    build_restock_embed,
    build_system_embed,
)

PRODUCT = Product(
    name="Prismatic Evolutions ETB", retailer="bestbuy",
    url="https://www.bestbuy.ca/en-ca/product/x", max_price=Decimal("64.99"), sku="123",
)


def test_restock_embed():
    result = StockResult(status=Status.IN_STOCK, price=Decimal("64.99"))
    embed = build_restock_embed(PRODUCT, result)
    assert embed["color"] == COLOR_RESTOCK
    assert embed["url"] == PRODUCT.url
    assert "Prismatic Evolutions ETB" in embed["title"]
    assert "64.99" in embed["description"]




def test_system_embed():
    embed = build_system_embed("walmart is blocking checks")
    assert embed["color"] == COLOR_SYSTEM
    assert "walmart is blocking checks" in embed["description"]


def test_heartbeat_embed():
    embed = build_heartbeat_embed(12, [])
    assert embed["color"] == COLOR_INFO
    assert "12" in embed["description"]
    unhealthy = build_heartbeat_embed(12, ["walmart"])
    assert "walmart" in unhealthy["description"]


EMBED = {"title": "test", "color": COLOR_INFO, "description": "hello"}
WEBHOOK = "https://discord.test/api/webhooks/1/abc"


def test_send_posts_embed_once_on_success():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(204)

    notifier = Notifier(WEBHOOK, transport=httpx.MockTransport(handler))
    asyncio.run(notifier.send(EMBED))
    assert len(requests) == 1
    assert requests[0] == {"embeds": [EMBED]}


def test_send_retries_on_429_then_succeeds():
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        if count["n"] == 1:
            return httpx.Response(429, json={"retry_after": 0})
        return httpx.Response(204)

    notifier = Notifier(WEBHOOK, transport=httpx.MockTransport(handler))
    asyncio.run(notifier.send(EMBED))
    assert count["n"] == 2


def test_send_survives_non_numeric_retry_after(monkeypatch):
    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        if count["n"] == 1:
            return httpx.Response(429, json={"retry_after": []})
        return httpx.Response(204)

    notifier = Notifier(WEBHOOK, transport=httpx.MockTransport(handler))
    asyncio.run(notifier.send(EMBED))
    assert count["n"] == 2


def test_send_gives_up_after_three_failures_without_raising(monkeypatch):
    async def fast_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(500)

    notifier = Notifier(WEBHOOK, transport=httpx.MockTransport(handler))
    asyncio.run(notifier.send(EMBED))
    assert count["n"] == 3
