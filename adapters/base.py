from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

import httpx


class Status(Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


@dataclass
class Product:
    name: str
    retailer: str
    url: str
    max_price: Decimal
    sku: str | None = None

    @property
    def key(self) -> str:
        return f"{self.retailer}:{self.sku or self.url}"


@dataclass
class StockResult:
    status: Status
    price: Decimal | None = None
    title: str = ""
    url: str = ""


class Blocked(Exception):
    """Retailer returned a bot-block response (403/429/challenge page)."""


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


class Adapter(Protocol):
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult: ...


def raise_if_blocked(response: httpx.Response) -> None:
    if response.status_code in (403, 429):
        raise Blocked(f"{response.request.url.host} returned {response.status_code}")
