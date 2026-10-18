"""令牌桶限流器（借鉴 Gemini 方案的 AgnesRateLimiter，泛化为多模型）。

Agnes-2.5-Flash 限 20 RPM → 设 18 RPM 留缓冲；DeepSeek 等付费模型也走各自配额，
避免触发 429。
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict


class RateLimiter:
    """基于最小间隔的限流器（单用户场景等价于令牌桶，保证不超过 max_rpm）。"""

    def __init__(self, max_rpm: float):
        self.interval = 60.0 / max_rpm
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class RateLimitManager:
    """按模型名管理多个限流器。"""

    def __init__(self, quotas: Dict[str, float]):
        self._limiters: Dict[str, RateLimiter] = {
            name: RateLimiter(rpm) for name, rpm in quotas.items()
        }

    async def acquire(self, model_name: str) -> None:
        limiter = self._limiters.get(model_name)
        if limiter:
            await limiter.acquire()


def default_limiter() -> RateLimitManager:
    """默认配额：Agnes 18 RPM（留 2 缓冲），DeepSeek 60 RPM。"""
    return RateLimitManager({"agnes": 18.0, "deepseek": 60.0})
