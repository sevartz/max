from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Placement, Proposal, ProposalStatus

OPEN_STATUSES = (ProposalStatus.PENDING, ProposalStatus.APPROVED)


async def add(s: AsyncSession, proposal: Proposal) -> Proposal:
    s.add(proposal)
    await s.flush()
    return proposal


async def get(s: AsyncSession, proposal_id: int, *, for_update: bool = False) -> Proposal | None:
    return await s.get(
        Proposal, proposal_id, with_for_update=for_update, populate_existing=for_update
    )


async def latest_for_channel(s: AsyncSession, chat_id: int) -> Proposal | None:
    q = select(Proposal).where(Proposal.chat_id == chat_id).order_by(Proposal.id.desc()).limit(1)
    return await s.scalar(q)


async def has_open(s: AsyncSession, chat_id: int) -> bool:
    q = select(Proposal.id).where(Proposal.chat_id == chat_id, Proposal.status.in_(OPEN_STATUSES))
    return (await s.scalar(q.limit(1))) is not None


async def count_for_post(s: AsyncSession, post_id: int) -> int:
    q = select(func.count()).select_from(Proposal).where(Proposal.post_id == post_id)
    return int(await s.scalar(q) or 0)


async def rejected_ad_ids(s: AsyncSession, chat_id: int, since: datetime) -> set[int]:
    q = select(Proposal.ad_id).where(
        Proposal.chat_id == chat_id,
        Proposal.status == ProposalStatus.REJECTED,
        Proposal.decided_at >= since,
    )
    return set((await s.scalars(q)).all())


async def list_expired_pending(s: AsyncSession, now: datetime) -> list[Proposal]:
    q = (
        select(Proposal)
        .where(Proposal.status == ProposalStatus.PENDING, Proposal.expires_at < now)
        .with_for_update(skip_locked=True)
    )
    return list((await s.scalars(q)).all())


async def cancel_open_for_channel(s: AsyncSession, chat_id: int, now: datetime) -> list[Proposal]:
    q = select(Proposal).where(Proposal.chat_id == chat_id, Proposal.status.in_(OPEN_STATUSES))
    items = list((await s.scalars(q)).all())
    if items:
        await s.execute(
            update(Proposal)
            .where(Proposal.id.in_([p.id for p in items]))
            .values(status=ProposalStatus.CANCELLED, decided_at=now)
        )
    return items


async def placed_ad_ids(s: AsyncSession, chat_id: int, since: datetime) -> set[int]:
    q = select(Placement.ad_id).where(Placement.chat_id == chat_id, Placement.published_at >= since)
    return set((await s.scalars(q)).all())


async def last_placement_at(s: AsyncSession, chat_id: int) -> datetime | None:
    q = select(func.max(Placement.published_at)).where(Placement.chat_id == chat_id)
    return await s.scalar(q)


async def placements_since(s: AsyncSession, chat_id: int, since: datetime) -> int:
    q = (
        select(func.count())
        .select_from(Placement)
        .where(Placement.chat_id == chat_id, Placement.published_at >= since)
    )
    return int(await s.scalar(q) or 0)
