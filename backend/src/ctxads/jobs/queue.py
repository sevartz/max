"""Очередь задач в Postgres: `SELECT … FOR UPDATE SKIP LOCKED`, без Redis."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Job

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


async def enqueue(
    s: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    unique: bool = False,
) -> Job | None:
    """unique=True — не ставить, если такая же задача (kind+payload) уже ждёт."""
    payload = payload or {}
    if unique:
        q = select(Job.id).where(
            Job.kind == kind, Job.status.in_((PENDING, RUNNING)), Job.payload == payload
        )
        if (await s.scalar(q.limit(1))) is not None:
            return None
    job = Job(kind=kind, payload=payload, status=PENDING)
    if run_at is not None:
        job.run_at = run_at
    s.add(job)
    await s.flush()
    return job


async def claim(s: AsyncSession, now: datetime) -> Job | None:
    q = (
        select(Job)
        .where(Job.status == PENDING, Job.run_at <= now)
        .order_by(Job.run_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await s.scalar(q)
    if job is None:
        return None
    job.status = RUNNING
    job.attempts += 1
    await s.flush()
    return job


async def complete(s: AsyncSession, job_id: int) -> None:
    await s.execute(update(Job).where(Job.id == job_id).values(status=DONE, last_error=None))


async def fail(s: AsyncSession, job_id: int, error: str, retry_at: datetime | None) -> None:
    values: dict[str, Any] = {"last_error": error[:2000]}
    if retry_at is None:
        values["status"] = FAILED
    else:
        values.update(status=PENDING, run_at=retry_at)
    await s.execute(update(Job).where(Job.id == job_id).values(**values))


async def purge_finished(s: AsyncSession, before: datetime) -> None:
    await s.execute(delete(Job).where(Job.status == DONE, Job.run_at < before))


async def requeue_stale(s: AsyncSession) -> None:
    """После рестарта процесса «зависшие» running-задачи возвращаем в очередь."""
    await s.execute(update(Job).where(Job.status == RUNNING).values(status=PENDING))
