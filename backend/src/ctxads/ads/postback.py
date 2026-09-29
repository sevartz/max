"""GET /postback?click_id=…&amount=…&sig=… — конверсия от рекламодателя (CPA/реферальная модель).

sig = hex(HMAC-SHA256(postback_secret рекламодателя, f"{click_id}:{amount}")).
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Request

from ctxads.billing import ledger, pricing
from ctxads.context import AppContext
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import placements as placements_repo

router = APIRouter()


def sign(secret: str, click_id: str, amount: str) -> str:
    return hmac.new(secret.encode(), f"{click_id}:{amount}".encode(), hashlib.sha256).hexdigest()


class PostbackError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail


async def record_conversion(ctx: AppContext, click_id: str, amount: str, sig: str) -> Decimal:
    """Возвращает сумму начисления (0 — дубль или не CPA)."""
    try:
        value = Decimal(amount)
    except InvalidOperation as e:
        raise PostbackError(400, "bad amount") from e
    async with ctx.sessions.begin() as s:
        click = await placements_repo.get_click(s, click_id)
        if click is None:
            raise PostbackError(404, "unknown click")
        placement = await placements_repo.get(s, click.placement_id)
        assert placement is not None
        ad = await ads_repo.get(s, placement.ad_id)
        assert ad is not None
        advertiser = await ads_repo.get_advertiser(s, ad.advertiser_id)
        assert advertiser is not None
        if not hmac.compare_digest(sign(advertiser.postback_secret, click_id, amount), sig):
            raise PostbackError(403, "bad signature")
        if not await placements_repo.add_conversion(s, click_id, value):
            return Decimal(0)
        if ad.pricing_model != "cpa":
            return Decimal(0)
        return await ledger.charge(
            s,
            placement,
            kind="cpa",
            amount=pricing.cpa_charge(ad.price, value),
            ref=f"cpa:{click_id}",
            fee_rate=ctx.settings.platform_fee,
        )


@router.get("/postback")
async def postback(click_id: str, amount: str, sig: str, request: Request) -> dict[str, object]:
    ctx: AppContext = request.app.state.ctx
    try:
        charged = await record_conversion(ctx, click_id, amount, sig)
    except PostbackError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    return {"ok": True, "charged": str(charged)}
