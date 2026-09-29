from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """Скользящее окно: не более `rate` событий за `period` секунд. rate <= 0 — без лимита."""

    def __init__(self, rate: float, period: float = 1.0) -> None:
        self.rate = rate
        self.period = period
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.rate <= 0:
            return
        limit = max(1, int(self.rate))
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0] >= self.period:
                    self._events.popleft()
                if len(self._events) < limit:
                    self._events.append(now)
                    return
                await asyncio.sleep(self.period - (now - self._events[0]))


class KeyedRateLimiter:
    """Отдельный лимитер на ключ (например, chat_id: ≤ 2 сообщения/сек в один чат)."""

    def __init__(self, rate: float, period: float = 1.0) -> None:
        self.rate = rate
        self.period = period
        self._limiters: dict[str, RateLimiter] = {}

    async def acquire(self, key: str) -> None:
        limiter = self._limiters.get(key)
        if limiter is None:
            limiter = self._limiters[key] = RateLimiter(self.rate, self.period)
        await limiter.acquire()
