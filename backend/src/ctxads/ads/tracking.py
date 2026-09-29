"""GET /r/{token}: лог клика → 302 на рекламодателя с utm_* и click_id."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ctxads.billing import ledger, pricing
from ctxads.context import AppContext
from ctxads.db.models import Click
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import placements as placements_repo

router = APIRouter()

ANTIFRAUD_WINDOW = timedelta(hours=24)


def salted_hash(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()


def client_ip(request: Request) -> str:
    # За Caddy реальный IP приходит в X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def with_params(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit(parts._replace(query=urlencode(query)))


def utm_params(ad_key: str, chat_id: int) -> dict[str, str]:
    return {
        "utm_source": "max",
        "utm_medium": "channel_post",
        "utm_campaign": ad_key,
        "utm_content": str(chat_id),
    }


async def track_click(
    ctx: AppContext, token: str, ip: str, user_agent: str
) -> tuple[str, str] | None:
    """Возвращает (redirect_url, click_id) или None, если токен неизвестен."""
    salt = ctx.settings.click_salt.get_secret_value()
    ip_hash, ua_hash = salted_hash(salt, ip), salted_hash(salt, user_agent)
    async with ctx.sessions.begin() as s:
        placement = await placements_repo.get_by_token(s, token)
        if placement is None:
            return None
        ad = await ads_repo.get(s, placement.ad_id)
        assert ad is not None
        is_unique = not await placements_repo.has_recent_click(
            s, placement.id, ip_hash, ctx.now() - ANTIFRAUD_WINDOW
        )
        click_id = uuid.uuid4().hex
        await placements_repo.add_click(
            s,
            Click(
                placement_id=placement.id,
                click_id=click_id,
                ip_hash=ip_hash,
                ua_hash=ua_hash,
                is_unique=is_unique,
                ts=ctx.now(),
            ),
        )
        if is_unique and ad.pricing_model == "cpc":
            await ledger.charge(
                s,
                placement,
                kind="cpc",
                amount=pricing.cpc_charge(ad.price),
                ref=f"cpc:{click_id}",
                fee_rate=ctx.settings.platform_fee,
            )
        url = with_params(ad.url, {**utm_params(ad.key, placement.chat_id), "click_id": click_id})
    return url, click_id


@router.get("/r/{token}")
async def redirect(token: str, request: Request) -> RedirectResponse:
    ctx: AppContext = request.app.state.ctx
    result = await track_click(
        ctx, token, client_ip(request), request.headers.get("user-agent", "")
    )
    if result is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(result[0], status_code=302)
