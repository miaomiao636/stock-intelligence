# -*- coding: utf-8 -*-
"""Real-time price cache with TTL"""
import time
from typing import Dict, Optional, Tuple

class PriceCache:
    def __init__(self, ttl: int = 60):
        self._cache: Dict[str, Tuple[float, float]] = {}
        self._ttl = ttl

    def get(self, code: str) -> Optional[float]:
        if code in self._cache:
            price, ts = self._cache[code]
            if time.time() - ts < self._ttl:
                return price
            del self._cache[code]
        return None

    def set(self, code: str, price: float):
        self._cache[code] = (price, time.time())

    def get_batch(self, codes: list) -> Dict[str, float]:
        result = {}
        for code in codes:
            p = self.get(code)
            if p is not None:
                result[code] = p
        return result

    def set_batch(self, prices: Dict[str, float]):
        now = time.time()
        for code, price in prices.items():
            self._cache[code] = (price, now)

    def invalidate(self, code: str = None):
        if code:
            self._cache.pop(code, None)
        else:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

price_cache = PriceCache(ttl=60)
