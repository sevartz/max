"""Реестр задач воркера: kind → coroutine(ctx, payload)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from ctxads.ads import publisher
from ctxads.ads import stats as ad_stats
from ctxads.bot import texts
from ctxads.bot.dispatcher import Dispatcher
from ctxads.bot.views import proposal_base_text
from ctxads.context import AppContext
from ctxads.db.models import MiniAppIdempotency, ProposalStatus
from ctxads.db.repo import proposals as proposals_repo
from ctxads.db.repo import updates as updates_repo
from ctxads.jobs import queue
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Update

log = logging.getLogger(__name__)

Task = Callable[[AppContext, dict[str, Any]], Awaitable[None]]


async def handle_update(ctx: AppContext, payload: dict[str, Any]) -> None:
    await Dispatcher(ctx).dispatch(Update.model_validate(payload))


async def publish(ctx: AppContext, payload: dict[str, Any]) -> None:
    await publisher.publish(ctx, int(payload["proposal_id"]))


async def delete_ad(ctx: AppContext, payload: dict[str, Any]) -> None:
    await publisher.delete_ad(ctx, int(payload["placement_id"]))


async def poll_views(ctx: AppContext, payload: dict[str, Any]) -> None:
    await ad_stats.poll_views(ctx)


async def expire_proposals(ctx: AppContext, payload: dict[str, Any]) -> None:
    """Истёкшие pending-предложения → expired, у сообщения админу убираем кнопки."""
    async with ctx.sessions.begin() as s:
        expired = await proposals_repo.list_expired_pending(s, ctx.now())
        edits = []
        for p in expired:
            p.status = ProposalStatus.EXPIRED
            if p.admin_message_mid:
                edits.append((p.admin_message_mid, await proposal_base_text(s, p)))
    for mid, base in edits:
        try:
            await ctx.max.edit_message(mid, text=texts.expired(base), attachments=[])
        except MaxApiError as e:
            log.info("cannot edit expired proposal message %s: %s", mid, e)


async def purge_updates(ctx: AppContext, payload: dict[str, Any]) -> None:
    async with ctx.sessions.begin() as s:
        week_ago = ctx.now() - timedelta(days=7)
        await updates_repo.purge_older_than(s, week_ago)
        await queue.purge_finished(s, week_ago)
        await s.execute(
            MiniAppIdempotency.__table__.delete().where(
                MiniAppIdempotency.created_at < ctx.now() - timedelta(days=30)
            )
        )


TASKS: dict[str, Task] = {
    "update": handle_update,
    "publish": publish,
    "delete_ad": delete_ad,
    "poll_views": poll_views,
    "expire_proposals": expire_proposals,
    "purge_updates": purge_updates,
}
