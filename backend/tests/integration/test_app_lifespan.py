from __future__ import annotations

import asyncio

from sqlalchemy import select

from ctxads.app import create_app
from ctxads.db.models import Job
from tests.conftest import BOT_ID, OWNER_ID, load_update


async def test_polling_app_runs_worker_scheduler_and_poller(ctx, max_mock):
    ctx.bot_user_id = None
    max_mock.updates = [load_update("bot_started")]
    app = create_app("polling", ctx.settings, ctx)

    async with app.router.lifespan_context(app):
        for _ in range(50):
            if max_mock.sent(user_id=OWNER_ID):
                break
            await asyncio.sleep(0.05)

    assert ctx.bot_user_id == BOT_ID, "id бота берётся из GET /me при старте"
    assert max_mock.calls("GET", "/updates"), "long polling запущен"
    assert max_mock.sent(user_id=OWNER_ID), "апдейт прошёл poller → jobs → worker → dispatcher"
    async with ctx.sessions() as s:
        kinds = set((await s.scalars(select(Job.kind))).all())
    assert {"update", "expire_proposals", "poll_views"} <= kinds


async def test_webhook_app_subscribes(ctx, max_mock):
    app = create_app(
        "webhook",
        ctx.settings.model_copy(update={"webhook_public_url": "https://bot.example/webhook/max"}),
        ctx,
    )
    async with app.router.lifespan_context(app):
        pass
    [req] = max_mock.calls("POST", "/subscriptions")
    assert b"https://bot.example/webhook/max" in req.content
    assert b"s3cret" in req.content
    assert max_mock.calls("GET", "/updates") == [], "при webhook long polling не запускаем"
