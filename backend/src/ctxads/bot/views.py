"""Сборка сообщений, которым нужны данные из нескольких таблиц."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.bot import keyboards, texts
from ctxads.db.models import Proposal
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import posts as posts_repo


async def proposal_base_text(s: AsyncSession, proposal: Proposal) -> str:
    text, _ = await proposal_message(s, proposal)
    return text


async def proposal_message(s: AsyncSession, proposal: Proposal) -> tuple[str, keyboards.Keyboard]:
    post = await posts_repo.get(s, proposal.post_id)
    ad = await ads_repo.get(s, proposal.ad_id)
    channel = await channels_repo.get(s, proposal.chat_id)
    assert post is not None and ad is not None
    title = (channel.title if channel else None) or texts.untitled(proposal.chat_id)
    text = texts.proposal(
        post_text=post.text,
        channel_title=title,
        ad_title=ad.title,
        reason=proposal.reason,
        pricing_model=ad.pricing_model,
        price=ad.price,
        expected=proposal.expected_payout,
        expires_at=proposal.expires_at,
    )
    return text, keyboards.proposal(proposal.id, ad.category)
