"""Периодические задачи: expire_proposals, poll_views, purge_updates → в очередь jobs."""

from __future__ import annotations

import asyncio
import logging
import time

from ctxads.context import AppContext
from ctxads.jobs import queue

log = logging.getLogger(__name__)

SCHEDULE: dict[str, float] = {
    "expire_proposals": 60,
    "poll_views": 30 * 60,
    "purge_updates": 24 * 60 * 60,
}


class Scheduler:
    def __init__(self, ctx: AppContext, schedule: dict[str, float] | None = None) -> None:
        self.ctx = ctx
        self.schedule = schedule or SCHEDULE
        self._last: dict[str, float] = {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def tick(self) -> list[str]:
        now = time.monotonic()
        due = [k for k, every in self.schedule.items() if now - self._last.get(k, -1e18) >= every]
        if due:
            async with self.ctx.sessions.begin() as s:
                for kind in due:
                    await queue.enqueue(s, kind, {}, unique=True)
            for kind in due:
                self._last[kind] = now
        return due

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except TimeoutError:
                pass
