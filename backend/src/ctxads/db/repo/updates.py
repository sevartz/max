from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import ProcessedUpdate


async def mark_processed(s: AsyncSession, dedupe_key: str) -> bool:
    """True — апдейт новый; False — дубль (ретрай webhook/повтор polling)."""
    stmt = (
        insert(ProcessedUpdate)
        .values(dedupe_key=dedupe_key[:255])
        .on_conflict_do_nothing()
        .returning(ProcessedUpdate.dedupe_key)
    )
    return (await s.scalar(stmt)) is not None


async def purge_older_than(s: AsyncSession, before: datetime) -> None:
    await s.execute(delete(ProcessedUpdate).where(ProcessedUpdate.ts < before))
