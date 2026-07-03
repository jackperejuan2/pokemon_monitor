from __future__ import annotations

import json
from decimal import Decimal

import httpx

from .base import Product, Status, StockResult, raise_if_blocked

AVAILABILITY_URL = "https://www.bestbuy.ca/ecomm-api/availability/products"
OFFERS_URL = "https://www.bestbuy.ca/api/offers/v1/products/{sku}/offers"


def parse_availability(payload: dict) -> Status:
    availabilities = payload.get("availabilities") or []
    if not availabilities:
        return Status.UNKNOWN
    shipping = availabilities[0].get("shipping") or {}
    purchasable = shipping.get("purchasable", False)
    status_text = str(shipping.get("status", "")).lower()
    if purchasable and "instock" in status_text:
        return Status.IN_STOCK
    return Status.OUT_OF_STOCK


def parse_price(offers: list) -> Decimal | None:
    if not offers:
        return None
    offer = offers[0]
    raw = offer.get("salePrice") or offer.get("regularPrice")
    return Decimal(str(raw)) if raw is not None else None


class BestBuyAdapter:
    async def check(self, client: httpx.AsyncClient, product: Product) -> StockResult:
        response = await client.get(
            AVAILABILITY_URL,
            params={
                "accept": "application/vnd.bestbuy.standardproduct.v1+json",
                "skus": product.sku,
            },
        )
        raise_if_blocked(response)
        response.raise_for_status()
        # BestBuy.ca prepends a UTF-8 BOM to this endpoint's JSON
        payload = json.loads(response.text.lstrip("﻿"))
        status = parse_availability(payload)

        price: Decimal | None = None
        if status is Status.IN_STOCK:
            offers_resp = await client.get(OFFERS_URL.format(sku=product.sku))
            if offers_resp.status_code == 200:
                price = parse_price(offers_resp.json())
        return StockResult(status=status, price=price, title=product.name, url=product.url)
