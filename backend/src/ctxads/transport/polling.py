"""Long polling (GET /updates) — только для локальной разработки."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from ctxads.context import AppContext
from ctxads.max_api.errors import MaxApiError
from ctxads.transport.ingest import ingest

log = logging.getLogger(__name__)


class Poller:
    def __init__(self, ctx: AppContext, timeout: int = 30) -> None:
        self.ctx = ctx
        self.timeout = timeout
        self.marker: int | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def poll_once(self) -> int:
        result = await self.ctx.max.get_updates(marker=self.marker, timeout=self.timeout)
        n = 0
        for update in result.updates:
            try:
                if await ingest(
                    self.ctx.sessions, update.model_dump(mode="json", exclude_none=True)
                ):
                    n += 1
            except ValidationError:
                log.warning("polling: malformed update skipped")
        if result.marker is not None:
            self.marker = result.marker
        return n

    async def run(self) -> None:
        log.info("long polling started")
        while not self._stop.is_set():
            pause = 0.0
            try:
                if await self.poll_once() == 0:
                    pause = 0.2  # пустой ответ: не крутим горячий цикл
            except MaxApiError as e:
                log.warning("polling error: %s", e)
                pause = 3
            except Exception:
                log.exception("polling crashed, restarting in 3s")
                pause = 3
            if pause:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=pause)
                except TimeoutError:
                    pass
