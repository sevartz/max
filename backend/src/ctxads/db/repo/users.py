from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Consent, User


async def upsert_user(s: AsyncSession, user_id: int, name: str | None) -> None:
    stmt = insert(User).values(max_user_id=user_id, name=name)
    stmt = stmt.on_conflict_do_update(index_elements=[User.max_user_id], set_={"name": name})
    await s.execute(stmt)


async def has_consent(s: AsyncSession, user_id: int, version: str) -> bool:
    q = select(Consent.id).where(Consent.user_id == user_id, Consent.version == version)
    return (await s.scalar(q)) is not None


async def add_consent(s: AsyncSession, user_id: int, version: str) -> bool:
    """True, если согласие записано впервые."""
    stmt = (
        insert(Consent)
        .values(user_id=user_id, version=version)
        .on_conflict_do_nothing()
        .returning(Consent.id)
    )
    return (await s.scalar(stmt)) is not None
