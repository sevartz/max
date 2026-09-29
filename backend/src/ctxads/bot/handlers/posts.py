from __future__ import annotations

import logging
from datetime import UTC, datetime

from ctxads.bot import texts
from ctxads.bot.views import proposal_message
from ctxads.context import AppContext
from ctxads.db.models import ChannelStatus, ProposalStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.db.repo import posts as posts_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.matching import pipeline
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Message

log = logging.getLogger(__name__)

# Префикс маркировки до первого подставляемого поля, без «#»: «Реклама. » находит и новые
# посты с «#Реклама.», и рекламу, опубликованную до смены формата.
AD_MARKER_PREFIX = texts.AD_MARKING.split("{", 1)[0].lstrip("#")


async def on_channel_post(ctx: AppContext, msg: Message) -> None:
    chat_id, mid = msg.recipient.chat_id, msg.mid
    if chat_id is None or mid is None:
        return
    async with ctx.sessions.begin() as s:
        # Блокировка строки канала сериализует обработку постов одного канала.
        channel = await channels_repo.get(s, chat_id, for_update=True)
        if channel is None or channel.status != ChannelStatus.ACTIVE:
            return
        is_our_ad = await placements_repo.is_our_ad_mid(s, mid) or _looks_like_our_ad(msg.text)
        created = datetime.fromtimestamp(msg.timestamp / 1000, UTC) if msg.timestamp else ctx.now()
        post = await posts_repo.create(
            s, chat_id=chat_id, mid=mid, text=msg.text, created_at=created, is_our_ad=is_our_ad
        )
        if post is None or is_our_ad:
            return
        result = await pipeline.process_post(ctx, s, channel, post)
        log.info("post %s in %s: %s", mid, chat_id, result.reason)
        if result.proposal is None:
            return
        proposal_id = result.proposal.id
        owner = channel.owner_user_id
        text, keyboard = await proposal_message(s, result.proposal)

    try:
        sent = await ctx.max.send_message(user_id=owner, text=text, attachments=[keyboard])
    except MaxApiError as e:
        log.warning("cannot send proposal %s to %s: %s", proposal_id, owner, e)
        async with ctx.sessions.begin() as s:
            proposal = await proposals_repo.get(s, proposal_id)
            if proposal is not None:  # не держим канал «занятым» предложением, которого не видно
                proposal.status = ProposalStatus.CANCELLED
        return
    if sent is not None and sent.mid:
        async with ctx.sessions.begin() as s:
            proposal = await proposals_repo.get(s, proposal_id)
            if proposal is not None:
                proposal.admin_message_mid = sent.mid


def _looks_like_our_ad(text: str) -> bool:
    """Страховка на гонку: событие о нашей рекламе может прийти раньше, чем сохранён mid."""
    return AD_MARKER_PREFIX in text and "erid:" in text
