from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Click, Conversion, LedgerEntry, Placement


async def add(s: AsyncSession, placement: Placement) -> Placement:
    s.add(placement)
    await s.flush()
    return placement


async def get(s: AsyncSession, placement_id: int) -> Placement | None:
    return await s.get(Placement, placement_id)


async def get_by_token(s: AsyncSession, token: str) -> Placement | None:
    return await s.scalar(select(Placement).where(Placement.token == token))


async def get_by_proposal(s: AsyncSession, proposal_id: int) -> Placement | None:
    return await s.scalar(select(Placement).where(Placement.proposal_id == proposal_id))


async def is_our_ad_mid(s: AsyncSession, mid: str) -> bool:
    return (await s.scalar(select(Placement.id).where(Placement.mid == mid))) is not None


async def list_for_views(s: AsyncSession, since: datetime) -> list[Placement]:
    q = select(Placement).where(
        Placement.published_at >= since,
        Placement.deleted_at.is_(None),
        Placement.mid.is_not(None),
    )
    return list((await s.scalars(q)).all())


async def has_recent_click(
    s: AsyncSession, placement_id: int, ip_hash: str, since: datetime
) -> bool:
    q = select(Click.id).where(
        Click.placement_id == placement_id, Click.ip_hash == ip_hash, Click.ts >= since
    )
    return (await s.scalar(q.limit(1))) is not None


async def add_click(s: AsyncSession, click: Click) -> Click:
    s.add(click)
    await s.flush()
    return click


async def get_click(s: AsyncSession, click_id: str) -> Click | None:
    return await s.scalar(select(Click).where(Click.click_id == click_id))


async def add_conversion(s: AsyncSession, click_id: str, amount: Decimal) -> bool:
    stmt = (
        insert(Conversion)
        .values(click_id=click_id, amount=amount)
        .on_conflict_do_nothing(index_elements=[Conversion.click_id])
        .returning(Conversion.id)
    )
    return (await s.scalar(stmt)) is not None


async def add_ledger_entry(s: AsyncSession, entry: LedgerEntry) -> bool:
    """False, если начисление с таким ref уже было (идемпотентность)."""
    stmt = (
        insert(LedgerEntry)
        .values(
            placement_id=entry.placement_id,
            ad_id=entry.ad_id,
            chat_id=entry.chat_id,
            kind=entry.kind,
            ref=entry.ref,
            advertiser_debit=entry.advertiser_debit,
            channel_credit=entry.channel_credit,
            platform_fee=entry.platform_fee,
        )
        .on_conflict_do_nothing(index_elements=[LedgerEntry.ref])
        .returning(LedgerEntry.id)
    )
    return (await s.scalar(stmt)) is not None


async def channel_stats(s: AsyncSession, chat_id: int, since: datetime) -> dict[str, object]:
    placements_q = select(
        func.count(Placement.id), func.coalesce(func.sum(Placement.views), 0)
    ).where(Placement.chat_id == chat_id, Placement.published_at >= since)
    n_placements, views = (await s.execute(placements_q)).one()
    clicks_q = (
        select(func.count(Click.id))
        .join(Placement, Click.placement_id == Placement.id)
        .where(Placement.chat_id == chat_id, Click.ts >= since, Click.is_unique)
    )
    clicks = await s.scalar(clicks_q)
    earned_q = select(func.coalesce(func.sum(LedgerEntry.channel_credit), 0)).where(
        LedgerEntry.chat_id == chat_id, LedgerEntry.ts >= since
    )
    earned = await s.scalar(earned_q)
    return {
        "placements": int(n_placements or 0),
        "views": int(views or 0),
        "clicks": int(clicks or 0),
        "earned": Decimal(earned or 0),
    }


async def ad_stats(
    s: AsyncSession, ad_ids: Sequence[int] | None = None
) -> dict[int, dict[str, object]]:
    """Сводка по объявлениям (все или только ad_ids) для витрины и кабинета рекламодателя."""
    placements_q = select(
        Placement.ad_id,
        func.count(Placement.id),
        func.coalesce(func.sum(Placement.views), 0),
    ).group_by(Placement.ad_id)
    clicks_q = (
        select(Placement.ad_id, func.count(Click.id))
        .join(Placement, Click.placement_id == Placement.id)
        .where(Click.is_unique)
        .group_by(Placement.ad_id)
    )
    spent_q = select(LedgerEntry.ad_id, func.sum(LedgerEntry.advertiser_debit)).group_by(
        LedgerEntry.ad_id
    )
    if ad_ids is not None:
        placements_q = placements_q.where(Placement.ad_id.in_(ad_ids))
        clicks_q = clicks_q.where(Placement.ad_id.in_(ad_ids))
        spent_q = spent_q.where(LedgerEntry.ad_id.in_(ad_ids))
    rows = (await s.execute(placements_q)).all()
    stats: dict[int, dict[str, object]] = {
        ad_id: {"placements": int(n), "views": int(v), "clicks": 0, "spent": Decimal(0)}
        for ad_id, n, v in rows
    }
    clicks = (await s.execute(clicks_q)).all()
    for ad_id, n in clicks:
        stats.setdefault(ad_id, {"placements": 0, "views": 0, "clicks": 0, "spent": Decimal(0)})
        stats[ad_id]["clicks"] = int(n)
    spent = (await s.execute(spent_q)).all()
    for ad_id, amount in spent:
        stats.setdefault(ad_id, {"placements": 0, "views": 0, "clicks": 0, "spent": Decimal(0)})
        stats[ad_id]["spent"] = Decimal(amount or 0)
    return stats
