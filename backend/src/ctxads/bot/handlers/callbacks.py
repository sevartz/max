"""Нажатия кнопок. Payload: "p:<proposal_id>:<action>", "s:<chat_id>:…", "c:accept", "a:…"."""

from __future__ import annotations

import logging

from ctxads.ads import proposals as proposal_service
from ctxads.bot import texts
from ctxads.bot.handlers import cabinet, onboarding, settings
from ctxads.bot.views import proposal_message
from ctxads.context import AppContext
from ctxads.db.models import ProposalStatus
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Callback, Update

log = logging.getLogger(__name__)

ACTIONS = ("approve", "reject", "next", "block_cat")


async def on_callback(ctx: AppContext, upd: Update) -> None:
    cb = upd.callback
    if cb is None or not cb.payload:
        return
    prefix = cb.payload.split(":", 1)[0]
    if prefix == "p":
        await on_proposal_callback(ctx, cb)
    elif prefix == "s":
        await settings.on_settings_callback(ctx, cb, upd.message)
    elif prefix == "c":
        await onboarding.on_consent_callback(ctx, cb, upd.message)
    elif prefix == "a":
        await cabinet.on_callback(ctx, cb, upd.message)
    else:
        await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_STALE)


def parse_proposal_payload(payload: str) -> tuple[int, str] | None:
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "p" or parts[2] not in ACTIONS:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


async def on_proposal_callback(ctx: AppContext, cb: Callback) -> None:
    parsed = parse_proposal_payload(cb.payload or "")
    if parsed is None:
        await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_STALE)
        return
    proposal_id, action = parsed

    result: proposal_service.ProposalActionResult
    async with ctx.sessions.begin() as s:
        result = await proposal_service.act_on_proposal(
            ctx,
            s,
            cb.user.user_id,
            proposal_id,
            action,  # type: ignore[arg-type]
        )
        mid = result.proposal.admin_message_mid if result.proposal else None
    await ctx.max.answer_callback(cb.callback_id, notification=_proposal_ack(result.code, action))
    edit = await proposal_action_edit(ctx, result)
    if edit is None:
        return
    if mid:
        edit_mid, text, keyboard = edit
        try:
            await ctx.max.edit_message(
                edit_mid, text=text, attachments=[keyboard] if keyboard else []
            )
        except MaxApiError as e:
            log.warning("cannot edit admin message %s: %s", mid, e)


EditResult = tuple[str | None, str, dict | None] | None


def _proposal_ack(code: str, action: str) -> str:
    if code == "not_owner":
        return texts.ACK_NOT_OWNER
    if code in ("not_found", "stale", "expired"):
        return texts.ACK_STALE
    return texts.ACK_WORKING if action == "next" else texts.ACK_OK


async def proposal_action_edit(
    ctx: AppContext, result: proposal_service.ProposalActionResult
) -> EditResult:
    proposal = result.proposal
    if proposal is None or result.code in ("not_found", "not_owner"):
        return None
    async with ctx.sessions() as s:
        base, keyboard = await proposal_message(s, proposal)
        code = result.code
        if code == "stale":
            code = {
                ProposalStatus.APPROVED: "approved",
                ProposalStatus.REJECTED: "rejected",
                ProposalStatus.EXPIRED: "expired",
                ProposalStatus.CANCELLED: "cancelled",
                ProposalStatus.PUBLISHED: "published",
            }.get(proposal.status, "stale")
        if code == "expired":
            text, kb = texts.expired(base), None
        elif code == "approved":
            text, kb = texts.approved(base, proposal.publish_at), None
        elif code == "rejected":
            text, kb = texts.rejected(base), None
        elif code == "category_blocked":
            ad = await ads_repo.get(s, proposal.ad_id)
            text = texts.category_blocked(base, ad.category if ad else "")
            kb = None
        elif code == "cancelled":
            text, kb = texts.cancelled(base), None
        elif code == "published":
            ad = await ads_repo.get(s, proposal.ad_id)
            channel = await channels_repo.get(s, proposal.chat_id)
            text = texts.published(
                ad.title if ad else "",
                (channel.title if channel else None) or texts.untitled(proposal.chat_id),
            )
            kb = None
        elif code == "no_more":
            text, kb = texts.superseded_no_more(base), keyboard
        elif code == "next":
            text, kb = base, keyboard
        else:
            return None
    return proposal.admin_message_mid, text, kb
