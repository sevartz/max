from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.billing import pricing
from ctxads.config import Settings
from ctxads.db.models import Ad, Channel
from ctxads.db.repo import ads as ads_repo
from ctxads.embeddings.base import Embedder, calibrate, cosine
from ctxads.matching.policy import Candidate

TOP_K = 20


def expected_gross(settings: Settings, ad: Ad, subscribers: int) -> Decimal:
    return pricing.expected_gross(
        ad.pricing_model,
        ad.price,
        subscribers,
        reach=settings.expected_reach,
        ctr=settings.expected_ctr,
        cr=settings.expected_cr,
    )


async def retrieve(
    s: AsyncSession,
    settings: Settings,
    embedder: Embedder,
    channel: Channel,
    post_embedding: Sequence[float],
    top_k: int = TOP_K,
) -> list[Candidate]:
    """pgvector cosine top-K по активным объявлениям → кандидаты для policy и скоринга."""
    found = await ads_repo.search_similar(s, post_embedding, top_k)
    lo, hi = embedder.sim_floor, embedder.sim_ceil
    return [
        Candidate(
            ad_id=ad.id,
            category=ad.category,
            sim_post=calibrate(sim, lo, hi),
            sim_channel=calibrate(cosine(channel.profile_embedding, ad.embedding), lo, hi),
            expected_gross=expected_gross(settings, ad, channel.subscribers or 0),
            targeting=ad.targeting or {},
            status=ad.status,
            budget_left=ad.budget_left,
        )
        for ad, sim in found
    ]
