from __future__ import annotations

from .base import Adapter
from .bestbuy import BestBuyAdapter
from .jsonld import JsonLdAdapter
from .walmart import WalmartAdapter

ADAPTERS: dict[str, Adapter] = {
    "bestbuy": BestBuyAdapter(),
    "walmart": WalmartAdapter(),
    "toysrus": JsonLdAdapter(),
    "indigo": JsonLdAdapter(),
    "ebgames": JsonLdAdapter(browser_first=True),
    "costco": JsonLdAdapter(),
}
