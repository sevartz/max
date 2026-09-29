"""Authenticated MAX mini-app API over the existing ctxads domain and database."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.ads import cabinet, channel_settings
from ctxads.ads import proposals as proposal_service
from ctxads.bot import texts
from ctxads.bot.handlers import callbacks as callback_handler
from ctxads.bot.handlers import channel as channel_handler
from ctxads.bot.handlers.settings import can_delete
from ctxads.context import AppContext
from ctxads.db.models import (
    Ad,
    Channel,
    ChannelStatus,
    MiniAppIdempotency,
    Proposal,
    ProposalStatus,
)
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.db.repo import posts as posts_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.db.repo import users as users_repo
from ctxads.matching.categories import CATEGORIES, REGULATED, label
from ctxads.max_api.errors import MaxApiError
from ctxads.miniapp.auth import MiniAppUser, validate_init_data

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
log = logging.getLogger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentInput(StrictModel):
    accepted: Literal[True]


class AdvertiserInput(StrictModel):
    name: str


class AdInput(StrictModel):
    title: str
    body: str
    url: str
    category: str
    pricing_model: str
    price: str
    budget: str


class AdFieldInput(StrictModel):
    field: Literal["title", "body", "url", "category", "price"]
    value: str


class AdStatusInput(StrictModel):
    paused: bool


class TopUpInput(StrictModel):
    amount: str


class ProposalActionInput(StrictModel):
    action: Literal["approve", "reject", "next", "block_cat"]


class CategoryInput(StrictModel):
    category: str
    allowed: bool


async def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def current_user(
    request: Request,
    x_max_init_data: Annotated[str | None, Header(alias="X-Max-Init-Data")] = None,
) -> MiniAppUser:
    ctx = await _ctx(request)
    token = ctx.settings.max_bot_token.get_secret_value()
    user = validate_init_data(x_max_init_data or "", token, now=ctx.now())
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_init_data",
                "message": "Откройте мини-приложение из MAX заново.",
            },
        )
    return user


async def require_consent(
    request: Request, user: Annotated[MiniAppUser, Depends(current_user)]
) -> MiniAppUser:
    ctx = await _ctx(request)
    async with ctx.sessions() as s:
        accepted = await users_repo.has_consent(s, user.user_id, ctx.settings.consent_version)
    if not accepted:
        raise HTTPException(
            status_code=403,
            detail={"code": "consent_required", "message": "Сначала примите условия."},
        )
    return user


@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    user: Annotated[MiniAppUser, Depends(current_user)],
) -> dict[str, object]:
    ctx = await _ctx(request)
    async with ctx.sessions.begin() as s:
        await users_repo.upsert_user(s, user.user_id, user.display_name)
        consented = await users_repo.has_consent(s, user.user_id, ctx.settings.consent_version)
        if not consented:
            return {
                "consented": False,
                "consent_text": texts.CONSENT,
                "consent_version": ctx.settings.consent_version,
            }
        channels = await channels_repo.list_by_owner(s, user.user_id)
        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id)
        channel_items = [await _channel_summary(s, ch, ctx) for ch in channels]
        ad_count = 0
        if advertiser is not None:
            ad_count = len(await ads_repo.list_by_advertiser(s, advertiser.id))
        return {
            "consented": True,
            "display_name": user.display_name,
            "channels": channel_items,
            "advertiser": {
                "exists": advertiser is not None,
                "name": advertiser.name if advertiser else None,
                "ad_count": ad_count,
            },
            "categories": [
                {"code": code, "label": category, "regulated": code in REGULATED}
                for code, category in CATEGORIES.items()
                if code != "other"
            ],
            "pricing_models": [
                {
                    "code": model,
                    "label": texts.PRICING_LABELS[model],
                    "unit": texts.PRICING_UNITS[model],
                }
                for model in cabinet.PRICING_MODELS
            ],
            "limits": {
                "company_name": cabinet.NAME_MAX,
                "title": cabinet.TITLE_MAX,
                "body": cabinet.BODY_MAX,
                "url": cabinet.URL_MAX,
            },
        }


@router.post("/consent")
async def accept_consent(
    request: Request,
    payload: ConsentInput,
    user: Annotated[MiniAppUser, Depends(current_user)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)

    async def apply(s: AsyncSession) -> dict[str, object]:
        await users_repo.upsert_user(s, user.user_id, user.display_name)
        await users_repo.add_consent(s, user.user_id, ctx.settings.consent_version)
        return {"accepted": payload.accepted}

    result = await _idempotent(ctx, user, key, "consent", apply)
    async with ctx.sessions() as s:
        pending = await channels_repo.list_pending_consent(s, user.user_id)
    for ch in pending:
        await channel_handler.connect(ctx, ch.chat_id, user.user_id)
    return result


@router.get("/channels/{chat_id}")
async def channel_detail(
    request: Request,
    chat_id: int,
    user: Annotated[MiniAppUser, Depends(require_consent)],
) -> dict[str, object]:
    ctx = await _ctx(request)
    async with ctx.sessions() as s:
        ch = await _owned_channel(s, chat_id, user.user_id)
        return await _channel_data(s, ch, ctx)


@router.post("/channels/{chat_id}/category")
async def update_channel_category(
    request: Request,
    chat_id: int,
    payload: CategoryInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)

    async def apply(s: AsyncSession) -> dict[str, object]:
        ch = await _owned_channel(s, chat_id, user.user_id, for_update=True)
        ok = await channel_settings.set_category_allowed(
            s, chat_id, payload.category, payload.allowed
        )
        if not ok:
            raise _field_error("category", "Выберите доступную категорию.")
        return await _channel_data(s, ch, ctx)

    return await _idempotent(ctx, user, key, f"channel:{chat_id}:category", apply)


@router.post("/channels/{chat_id}/pause")
async def update_channel_pause(
    request: Request,
    chat_id: int,
    payload: AdStatusInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)

    async def apply(s: AsyncSession) -> dict[str, object]:
        ch = await _owned_channel(s, chat_id, user.user_id, for_update=True)
        if not channel_settings.set_paused(ch, payload.paused):
            raise HTTPException(
                status_code=409, detail={"message": "Канал сейчас нельзя приостановить."}
            )
        return await _channel_data(s, ch, ctx)

    return await _idempotent(ctx, user, key, f"channel:{chat_id}:pause", apply)


@router.post("/proposals/{proposal_id}/action")
async def proposal_action(
    request: Request,
    proposal_id: int,
    payload: ProposalActionInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)

    async def apply(s: AsyncSession) -> dict[str, object]:
        result = await proposal_service.act_on_proposal(
            ctx, s, user.user_id, proposal_id, payload.action
        )
        if result.code in ("not_found", "not_owner"):
            raise HTTPException(status_code=404, detail={"message": "Предложение недоступно."})
        ch = await _owned_channel(s, result.proposal.chat_id, user.user_id)  # type: ignore[union-attr]
        data = await _channel_data(s, ch, ctx)
        data["action_result"] = result.code
        data["_action_proposal_id"] = result.proposal.id
        return data

    response = await _idempotent(ctx, user, key, f"proposal:{proposal_id}:{payload.action}", apply)
    action_proposal_id = response.pop("_action_proposal_id", None)
    action_result = response.get("action_result")
    if isinstance(action_proposal_id, int) and isinstance(action_result, str):
        await _sync_bot_proposal(ctx, action_proposal_id, action_result)
    return response


@router.get("/ads")
async def list_ads(
    request: Request, user: Annotated[MiniAppUser, Depends(require_consent)]
) -> dict[str, object]:
    ctx = await _ctx(request)
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id)
        if advertiser is None:
            return {"advertiser": None, "ads": []}
        ads = await ads_repo.list_by_advertiser(s, advertiser.id)
        stats = await placements_repo.ad_stats(s, [ad.id for ad in ads])
        return {
            "advertiser": {"name": advertiser.name},
            "ads": [_ad_data(ad, stats.get(ad.id, {})) for ad in ads],
        }


@router.post("/advertiser")
async def create_advertiser(
    request: Request,
    payload: AdvertiserInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)
    name = cabinet.parse_text(payload.name, cabinet.NAME_MAX)
    if name is None:
        raise _field_error("name", "Название компании или бренда: от 1 до 100 символов.")

    async def apply(s: AsyncSession) -> dict[str, object]:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id, for_update=True)
        if advertiser is None:
            advertiser = await cabinet.create_advertiser(s, user.user_id, name)
        return {"name": advertiser.name}

    return await _idempotent(ctx, user, key, "advertiser:create", apply)


@router.post("/ads")
async def create_ad(
    request: Request,
    payload: AdInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)
    draft = _validate_draft(payload)

    async def apply(s: AsyncSession) -> dict[str, object]:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id, for_update=True)
        if advertiser is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "advertiser_required",
                    "message": "Сначала укажите компанию или бренд.",
                },
            )
        ad = await cabinet.create_ad(s, ctx.embedder, advertiser, draft)
        return _ad_data(ad, {})

    return await _idempotent(ctx, user, key, "ad:create", apply)


@router.put("/ads/{ad_id}/field")
async def update_ad_field(
    request: Request,
    ad_id: int,
    payload: AdFieldInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)
    value: str | Decimal
    if payload.field == "title":
        value = cabinet.parse_text(payload.value, cabinet.TITLE_MAX) or _raise_field(
            "title", "Заголовок: от 1 до 100 символов."
        )
    elif payload.field == "body":
        value = cabinet.parse_text(payload.value, cabinet.BODY_MAX) or _raise_field(
            "body", "Текст: от 1 до 1000 символов."
        )
    elif payload.field == "url":
        value = cabinet.parse_url(payload.value) or _raise_field(
            "url", "Нужна ссылка вида https://example.com."
        )
    elif payload.field == "category":
        if payload.value not in cabinet.CATEGORY_CHOICES:
            raise _field_error("category", "Выберите доступную категорию.")
        value = payload.value
    else:
        value = cabinet.parse_money(payload.value) or _raise_field(
            "price", "Укажите сумму в рублях, например 250 или 1500,50."
        )

    async def apply(s: AsyncSession) -> dict[str, object]:
        ad = await _owned_ad(s, user.user_id, ad_id, for_update=True)
        await cabinet.update_field(ctx.embedder, ad, payload.field, value)
        stats = await placements_repo.ad_stats(s, [ad.id])
        return _ad_data(ad, stats.get(ad.id, {}))

    return await _idempotent(ctx, user, key, f"ad:{ad_id}:field:{payload.field}", apply)


@router.post("/ads/{ad_id}/pause")
async def update_ad_pause(
    request: Request,
    ad_id: int,
    payload: AdStatusInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)

    async def apply(s: AsyncSession) -> dict[str, object]:
        ad = await _owned_ad(s, user.user_id, ad_id, for_update=True)
        cabinet.set_paused(ad, payload.paused)
        stats = await placements_repo.ad_stats(s, [ad.id])
        return _ad_data(ad, stats.get(ad.id, {}))

    return await _idempotent(ctx, user, key, f"ad:{ad_id}:pause", apply)


@router.post("/ads/{ad_id}/topup")
async def top_up_ad(
    request: Request,
    ad_id: int,
    payload: TopUpInput,
    user: Annotated[MiniAppUser, Depends(require_consent)],
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    ctx = await _ctx(request)
    amount = cabinet.parse_money(payload.amount)
    if amount is None:
        raise _field_error("amount", "Нужна сумма в рублях, например 250 или 1500,50.")

    async def apply(s: AsyncSession) -> dict[str, object]:
        ad = await _owned_ad(s, user.user_id, ad_id, for_update=True)
        cabinet.top_up(ad, amount)
        stats = await placements_repo.ad_stats(s, [ad.id])
        response = _ad_data(ad, stats.get(ad.id, {}))
        response["topped_up"] = str(amount)
        return response

    return await _idempotent(ctx, user, key, f"ad:{ad_id}:topup", apply)


@router.get("/stats")
async def stats(
    request: Request, user: Annotated[MiniAppUser, Depends(require_consent)]
) -> dict[str, object]:
    ctx = await _ctx(request)
    now = ctx.now()
    async with ctx.sessions() as s:
        channels = await channels_repo.list_by_owner(s, user.user_id)
        channel_stats = []
        for ch in channels:
            periods = {}
            for days in (7, 30):
                st = await placements_repo.channel_stats(s, ch.chat_id, now - timedelta(days=days))
                periods[str(days)] = _channel_stats_data(st)
            channel_stats.append({"chat_id": ch.chat_id, "title": ch.title, "periods": periods})

        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id)
        advertiser_stats: dict[str, object] | None = None
        if advertiser is not None:
            ads = await ads_repo.list_by_advertiser(s, advertiser.id)
            ad_stats = await placements_repo.ad_stats(s, [ad.id for ad in ads])
            total = {"placements": 0, "views": 0, "clicks": 0, "spent": Decimal(0)}
            per_ad = []
            for ad in ads:
                st = ad_stats.get(ad.id, {})
                total["placements"] += int(st.get("placements", 0))
                total["views"] += int(st.get("views", 0))
                total["clicks"] += int(st.get("clicks", 0))
                total["spent"] += Decimal(st.get("spent", 0))
                per_ad.append(_ad_data(ad, st))
            advertiser_stats = {
                "name": advertiser.name,
                "summary": _numbers_to_strings(total),
                "ads": per_ad,
            }
        return {"channels": channel_stats, "advertiser": advertiser_stats}


async def _idempotent(
    ctx: AppContext,
    user: MiniAppUser,
    key: str | None,
    operation: str,
    action,
) -> dict[str, object]:
    if key is None or not _IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(
            status_code=400, detail={"message": "Не удалось подтвердить действие. Повторите его."}
        )
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    async with ctx.sessions.begin() as s:
        stmt = (
            insert(MiniAppIdempotency)
            .values(user_id=user.user_id, key_hash=key_hash, operation=operation)
            .on_conflict_do_nothing(index_elements=["user_id", "key_hash"])
            .returning(MiniAppIdempotency.id)
        )
        row_id = await s.scalar(stmt)
        if row_id is None:
            prior = await s.scalar(
                select(MiniAppIdempotency)
                .where(
                    MiniAppIdempotency.user_id == user.user_id,
                    MiniAppIdempotency.key_hash == key_hash,
                )
                .with_for_update()
            )
            if prior is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "mutation_in_progress",
                        "message": "Действие ещё обрабатывается. Проверьте результат чуть позже.",
                    },
                )
            if prior.operation != operation:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "idempotency_key_reused",
                        "message": "Не удалось подтвердить это действие. Обновите данные.",
                    },
                )
            if prior.response is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "mutation_in_progress",
                        "message": "Действие ещё обрабатывается. Проверьте результат чуть позже.",
                    },
                )
            return dict(prior.response)
        result = await action(s)
        record = await s.get(MiniAppIdempotency, row_id)
        assert record is not None
        record.response = result
        return result


async def _sync_bot_proposal(ctx: AppContext, proposal_id: int, code: str) -> None:
    async with ctx.sessions() as s:
        proposal = await proposals_repo.get(s, proposal_id)
        if proposal is None:
            return
        result = proposal_service.ProposalActionResult(code, proposal)  # type: ignore[arg-type]
        edit = await callback_handler.proposal_action_edit(ctx, result)
    if edit is None:
        return
    mid, text, keyboard = edit
    if mid is None:
        return
    try:
        await ctx.max.edit_message(mid, text=text, attachments=[keyboard] if keyboard else [])
    except MaxApiError as e:
        log.info("cannot sync mini-app proposal action %s to bot card: %s", proposal_id, e)


def _field_error(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"field": field, "message": message})


def _raise_field(field: str, message: str):
    raise _field_error(field, message)


def _validate_draft(payload: AdInput) -> dict[str, object]:
    title = cabinet.parse_text(payload.title, cabinet.TITLE_MAX)
    if title is None:
        raise _field_error("title", "Заголовок: от 1 до 100 символов.")
    body = cabinet.parse_text(payload.body, cabinet.BODY_MAX)
    if body is None:
        raise _field_error("body", "Текст: от 1 до 1000 символов.")
    url = cabinet.parse_url(payload.url)
    if url is None:
        raise _field_error("url", "Нужна ссылка вида https://example.com.")
    if payload.category not in cabinet.CATEGORY_CHOICES:
        raise _field_error("category", "Выберите доступную категорию.")
    if payload.pricing_model not in cabinet.PRICING_MODELS:
        raise _field_error("pricing_model", "Выберите модель оплаты.")
    price = cabinet.parse_money(payload.price)
    if price is None:
        raise _field_error("price", "Укажите сумму в рублях, например 250 или 1500,50.")
    budget = cabinet.parse_money(payload.budget)
    if budget is None:
        raise _field_error("budget", "Укажите сумму в рублях, например 250 или 1500,50.")
    if budget < price:
        raise _field_error("budget", f"Бюджет не может быть меньше цены ({texts.rub(price)}).")
    return {
        "title": title,
        "body": body,
        "url": url,
        "category": payload.category,
        "pricing_model": payload.pricing_model,
        "price": str(price),
        "budget": str(budget),
    }


async def _owned_channel(
    s: AsyncSession, chat_id: int, user_id: int, *, for_update: bool = False
) -> Channel:
    channel = await channels_repo.get(s, chat_id, for_update=for_update)
    if (
        channel is None
        or channel.owner_user_id != user_id
        or channel.status == ChannelStatus.REMOVED
    ):
        raise HTTPException(status_code=404, detail={"message": "Канал недоступен."})
    return channel


async def _owned_ad(s: AsyncSession, user_id: int, ad_id: int, *, for_update: bool = False) -> Ad:
    advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
    if advertiser is None:
        raise HTTPException(status_code=404, detail={"message": "Объявление недоступно."})
    ad = await (ads_repo.get_for_update(s, ad_id) if for_update else ads_repo.get(s, ad_id))
    if ad is None or ad.advertiser_id != advertiser.id:
        raise HTTPException(status_code=404, detail={"message": "Объявление недоступно."})
    return ad


async def _channel_summary(s: AsyncSession, channel: Channel, ctx: AppContext) -> dict[str, object]:
    proposal = await proposals_repo.latest_for_channel(s, channel.chat_id)
    return {
        "chat_id": channel.chat_id,
        "title": channel.title or texts.untitled(channel.chat_id),
        "status": channel.status,
        "subscribers": channel.subscribers,
        "proposal": await _proposal_data(s, proposal, ctx) if proposal else None,
    }


async def _channel_data(s: AsyncSession, channel: Channel, ctx: AppContext) -> dict[str, object]:
    settings = await channels_repo.get_settings(s, channel.chat_id)
    proposal = await proposals_repo.latest_for_channel(s, channel.chat_id)
    return {
        "chat_id": channel.chat_id,
        "title": channel.title or texts.untitled(channel.chat_id),
        "status": channel.status,
        "subscribers": channel.subscribers,
        "permissions": list(channel.bot_permissions or []),
        "has_required_permissions": all(
            permission in (channel.bot_permissions or [])
            for permission in channel_handler.REQUIRED_PERMISSIONS
        ),
        "can_delete": can_delete(channel.bot_permissions),
        "profile_summary": channel.profile_summary,
        "blocked_categories": list(settings.blocked_categories or []),
        "allow_regulated": list(settings.allow_regulated or []),
        "ad_ttl_hours": settings.ad_ttl_hours,
        "proposal": await _proposal_data(s, proposal, ctx) if proposal else None,
        "categories": [
            {
                "code": code,
                "label": category,
                "regulated": code in REGULATED,
                "allowed": (
                    code in (settings.allow_regulated or [])
                    and code not in (settings.blocked_categories or [])
                    if code in REGULATED
                    else code not in (settings.blocked_categories or [])
                ),
            }
            for code, category in CATEGORIES.items()
            if code != "other"
        ],
    }


async def _proposal_data(s: AsyncSession, proposal: Proposal, ctx: AppContext) -> dict[str, object]:
    post = await posts_repo.get(s, proposal.post_id)
    ad = await ads_repo.get(s, proposal.ad_id)
    now = ctx.now()
    status = proposal.status
    if status == ProposalStatus.PENDING and proposal.expires_at < now:
        status = ProposalStatus.EXPIRED
    total = await proposals_repo.count_for_post(s, proposal.post_id)
    return {
        "id": proposal.id,
        "status": status,
        "actionable": status == ProposalStatus.PENDING,
        "next_available": total <= ctx.settings.max_next_per_post,
        "post_text": post.text if post else "",
        "ad_title": ad.title if ad else "",
        "category": ad.category if ad else "",
        "category_label": label(ad.category) if ad else "",
        "reason": proposal.reason,
        "pricing_model": ad.pricing_model if ad else "",
        "pricing_label": texts.PRICING_LABELS.get(ad.pricing_model, ad.pricing_model) if ad else "",
        "pricing_unit": texts.PRICING_UNITS.get(ad.pricing_model, "") if ad else "",
        "price": str(ad.price) if ad else "0",
        "expected_income": str(proposal.expected_payout),
        "expires_at": proposal.expires_at.isoformat(),
        "publish_at": proposal.publish_at.isoformat() if proposal.publish_at else None,
    }


def _ad_data(ad: Ad, stats: dict[str, object]) -> dict[str, object]:
    status = "exhausted" if ad.status == cabinet.ACTIVE and ad.budget_left <= 0 else ad.status
    return {
        "id": ad.id,
        "title": ad.title,
        "body": ad.body,
        "url": ad.url,
        "category": ad.category,
        "category_label": label(ad.category),
        "pricing_model": ad.pricing_model,
        "pricing_label": texts.PRICING_LABELS.get(ad.pricing_model, ad.pricing_model),
        "pricing_unit": texts.PRICING_UNITS.get(ad.pricing_model, ""),
        "price": str(ad.price),
        "status": status,
        "budget_total": str(ad.budget_total),
        "budget_left": str(ad.budget_left),
        "placements": int(stats.get("placements", 0)),
        "views": int(stats.get("views", 0)),
        "clicks": int(stats.get("clicks", 0)),
        "spent": str(stats.get("spent", Decimal(0))),
    }


def _channel_stats_data(stats: dict[str, object]) -> dict[str, object]:
    return {
        "placements": int(stats["placements"]),
        "views": int(stats["views"]),
        "clicks": int(stats["clicks"]),
        "earned": str(stats["earned"]),
    }


def _numbers_to_strings(data: dict[str, object]) -> dict[str, object]:
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in data.items()}
