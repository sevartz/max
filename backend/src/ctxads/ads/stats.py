"""Опрос просмотров рекламных постов и CPM-начисления по приросту."""

from __future__ import annotations

import logging
from datetime import timedelta

from ctxads.billing import ledger, pricing
from ctxads.context import AppContext
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.max_api.errors import MaxApiError

log = logging.getLogger(__name__)

BATCH = 100


async def poll_views(ctx: AppContext) -> int:
    """Job `poll_views`: размещения младше views_poll_hours. Возвращает число обновлённых."""
    since = ctx.now() - timedelta(hours=ctx.settings.views_poll_hours)
    async with ctx.sessions() as s:
        items = [(p.id, p.mid) for p in await placements_repo.list_for_views(s, since)]
    updated = 0
    for i in range(0, len(items), BATCH):
        chunk = items[i : i + BATCH]
        try:
            messages = await ctx.max.get_messages(message_ids=[mid for _, mid in chunk if mid])
        except MaxApiError as e:
            log.warning("poll_views: %s", e)
            continue
        views_by_mid = {
            m.mid: m.stat.views for m in messages if m.mid and m.stat and m.stat.views is not None
        }
        for placement_id, mid in chunk:
            views = views_by_mid.get(mid)
            if views is not None and await apply_views(ctx, placement_id, views):
                updated += 1
    return updated


async def apply_views(ctx: AppContext, placement_id: int, views: int) -> bool:
    async with ctx.sessions.begin() as s:
        placement = await placements_repo.get(s, placement_id)
        if placement is None or views <= placement.views:
            return False
        ad = await ads_repo.get(s, placement.ad_id)
        assert ad is not None
        delta = views - placement.views
        if ad.pricing_model == "cpm":
            await ledger.charge(
                s,
                placement,
                kind="cpm",
                amount=pricing.cpm_charge(ad.price, delta),
                ref=f"cpm:{placement.id}:{views}",
                fee_rate=ctx.settings.platform_fee,
            )
        placement.views = views
    return True
