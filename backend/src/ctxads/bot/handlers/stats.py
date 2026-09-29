from __future__ import annotations

from datetime import timedelta

from ctxads.bot import keyboards, texts
from ctxads.context import AppContext
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import placements as placements_repo

PERIODS = (7, 30)


async def show(ctx: AppContext, user_id: int) -> None:
    now = ctx.now()
    lines: list[str] = []
    async with ctx.sessions() as s:
        channels = await channels_repo.list_by_owner(s, user_id)
        for ch in channels:
            title = ch.title or texts.untitled(ch.chat_id)
            for days in PERIODS:
                st = await placements_repo.channel_stats(s, ch.chat_id, now - timedelta(days=days))
                lines.append(texts.stats_block(title, days, st))
    app_button = keyboards.miniapp(ctx.settings.miniapp_url)
    await ctx.max.send_message(
        user_id=user_id,
        text="\n".join(lines) or texts.STATS_EMPTY,
        attachments=[app_button] if app_button else None,
    )
