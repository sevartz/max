"""Начисления: списание с бюджета объявления атомарно (SELECT … FOR UPDATE), запись в ledger."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.billing.pricing import money, split
from ctxads.db.models import LedgerEntry, Placement
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import placements as placements_repo

log = logging.getLogger(__name__)


async def charge(
    s: AsyncSession,
    placement: Placement,
    *,
    kind: str,
    amount: Decimal,
    ref: str,
    fee_rate: Decimal,
) -> Decimal:
    """Списывает min(amount, остаток бюджета).

    Возвращает фактически списанное: 0 — дубль (ref уже был) или бюджет исчерпан.
    """
    if amount <= 0:
        return Decimal(0)
    ad = await ads_repo.get_for_update(s, placement.ad_id)
    if ad is None:
        return Decimal(0)
    actual = money(min(amount, ad.budget_left))
    if actual <= 0:
        ad.status = "paused"
        return Decimal(0)
    parts = split(actual, fee_rate)
    inserted = await placements_repo.add_ledger_entry(
        s,
        LedgerEntry(
            placement_id=placement.id,
            ad_id=ad.id,
            chat_id=placement.chat_id,
            kind=kind,
            ref=ref,
            advertiser_debit=parts.advertiser_debit,
            channel_credit=parts.channel_credit,
            platform_fee=parts.platform_fee,
        ),
    )
    if not inserted:
        return Decimal(0)
    ad.budget_left = ad.budget_left - actual
    if ad.budget_left <= 0:
        ad.status = "paused"
        log.info("ad %s: budget exhausted, paused", ad.id)
    return actual
