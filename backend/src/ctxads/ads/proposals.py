"""Server-side proposal actions shared by MAX bot callbacks and the mini-app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.ads import channel_settings
from ctxads.context import AppContext
from ctxads.db.models import Proposal, ProposalStatus
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.jobs import queue
from ctxads.matching import pipeline

ProposalAction = Literal["approve", "reject", "next", "block_cat"]


@dataclass(frozen=True)
class ProposalActionResult:
    code: Literal[
        "not_found",
        "not_owner",
        "stale",
        "expired",
        "approved",
        "rejected",
        "category_blocked",
        "next",
        "no_more",
    ]
    proposal: Proposal | None = None


async def act_on_proposal(
    ctx: AppContext,
    s: AsyncSession,
    user_id: int,
    proposal_id: int,
    action: ProposalAction,
) -> ProposalActionResult:
    """Apply one owner-authorized decision under a proposal row lock."""
    proposal = await proposals_repo.get(s, proposal_id, for_update=True)
    if proposal is None:
        return ProposalActionResult("not_found")
    channel = await channels_repo.get(s, proposal.chat_id)
    if channel is None or channel.owner_user_id != user_id:
        return ProposalActionResult("not_owner", proposal)
    if proposal.status != ProposalStatus.PENDING:
        return ProposalActionResult("stale", proposal)
    if proposal.expires_at < ctx.now():
        proposal.status = ProposalStatus.EXPIRED
        return ProposalActionResult("expired", proposal)

    if action == "approve":
        settings = await channels_repo.get_settings(s, proposal.chat_id)
        proposal.status = ProposalStatus.APPROVED
        proposal.decided_at = ctx.now()
        proposal.publish_at = ctx.now() + timedelta(minutes=settings.publish_delay_min)
        await queue.enqueue(s, "publish", {"proposal_id": proposal.id}, run_at=proposal.publish_at)
        return ProposalActionResult("approved", proposal)

    if action == "reject":
        proposal.status = ProposalStatus.REJECTED
        proposal.decided_at = ctx.now()
        return ProposalActionResult("rejected", proposal)

    if action == "block_cat":
        ad = await ads_repo.get(s, proposal.ad_id)
        if ad is None:
            return ProposalActionResult("stale", proposal)
        await channel_settings.set_category_allowed(s, proposal.chat_id, ad.category, False)
        proposal.status = ProposalStatus.REJECTED
        proposal.decided_at = ctx.now()
        return ProposalActionResult("category_blocked", proposal)

    next_proposal = await pipeline.next_proposal(ctx, s, proposal)
    if next_proposal is None:
        return ProposalActionResult("no_more", proposal)
    proposal.status = ProposalStatus.SUPERSEDED
    proposal.decided_at = ctx.now()
    next_proposal.admin_message_mid = proposal.admin_message_mid
    return ProposalActionResult("next", next_proposal)
