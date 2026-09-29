"""In-process воркер: берёт задачи из таблицы jobs и выполняет их."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from ctxads.context import AppContext
from ctxads.jobs import queue
from ctxads.jobs.tasks import TASKS, Task

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self, ctx: AppContext, tasks: dict[str, Task] | None = None, *, concurrency: int = 4
    ) -> None:
        self.ctx = ctx
        self.tasks = tasks or TASKS
        self.concurrency = concurrency
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        async with self.ctx.sessions.begin() as s:
            await queue.requeue_stale(s)
        await asyncio.gather(*(self._loop() for _ in range(self.concurrency)))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did = await self.run_one()
            except Exception:
                log.exception("worker loop error")
                did = False
            if not did:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.ctx.settings.worker_poll_interval_sec
                    )
                except TimeoutError:
                    pass

    async def run_one(self) -> bool:
        """Выполнить одну готовую задачу. False — очередь пуста."""
        async with self.ctx.sessions.begin() as s:
            job = await queue.claim(s, self.ctx.now())
            if job is None:
                return False
            job_id, kind, payload, attempts = job.id, job.kind, dict(job.payload), job.attempts
        task = self.tasks.get(kind)
        try:
            if task is None:
                raise LookupError(f"unknown job kind {kind}")
            await task(self.ctx, payload)
        except Exception as e:
            log.exception("job %s (%s) failed, attempt %s", job_id, kind, attempts)
            retry_at = None
            if attempts < self.ctx.settings.job_max_attempts and task is not None:
                retry_at = self.ctx.now() + timedelta(seconds=5 * 2 ** (attempts - 1))
            async with self.ctx.sessions.begin() as s:
                await queue.fail(s, job_id, f"{type(e).__name__}: {e}", retry_at)
        else:
            async with self.ctx.sessions.begin() as s:
                await queue.complete(s, job_id)
        return True

    async def drain(self, max_jobs: int = 100) -> int:
        """Для тестов и демо: выполнить всё, что готово сейчас."""
        n = 0
        while n < max_jobs and await self.run_one():
            n += 1
        return n
