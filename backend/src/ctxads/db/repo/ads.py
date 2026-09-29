from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Ad, Advertiser


async def search_similar(
    s: AsyncSession, embedding: Sequence[float], limit: int = 20
) -> list[tuple[Ad, float]]:
    """Top-N активных объявлений по косинусной близости к эмбеддингу."""
    distance = Ad.embedding.cosine_distance(list(embedding)).label("distance")
    q = (
        select(Ad, distance)
        .where(Ad.status == "active", Ad.embedding.is_not(None), Ad.budget_left > 0)
        .order_by(distance)
        .limit(limit)
    )
    rows = (await s.execute(q)).all()
    return [(ad, 1.0 - float(dist)) for ad, dist in rows]


async def get(s: AsyncSession, ad_id: int) -> Ad | None:
    return await s.get(Ad, ad_id)


async def get_for_update(s: AsyncSession, ad_id: int) -> Ad | None:
    return await s.get(Ad, ad_id, with_for_update=True, populate_existing=True)


async def get_advertiser(s: AsyncSession, advertiser_id: int) -> Advertiser | None:
    return await s.get(Advertiser, advertiser_id)


async def list_all(s: AsyncSession) -> list[tuple[Ad, Advertiser]]:
    q = select(Ad, Advertiser).join(Advertiser, Ad.advertiser_id == Advertiser.id).order_by(Ad.id)
    return [(a, adv) for a, adv in (await s.execute(q)).all()]


async def get_advertiser_by_owner(
    s: AsyncSession, user_id: int, *, for_update: bool = False
) -> Advertiser | None:
    q = select(Advertiser).where(Advertiser.owner_user_id == user_id)
    if for_update:
        q = q.with_for_update()
    return await s.scalar(q)


async def add_advertiser(s: AsyncSession, advertiser: Advertiser) -> Advertiser:
    s.add(advertiser)
    await s.flush()
    return advertiser


async def list_by_advertiser(s: AsyncSession, advertiser_id: int) -> list[Ad]:
    q = select(Ad).where(Ad.advertiser_id == advertiser_id).order_by(Ad.id)
    return list((await s.scalars(q)).all())


async def add(s: AsyncSession, ad: Ad) -> Ad:
    s.add(ad)
    await s.flush()
    return ad
