from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ctxads.ads import postback, showcase, tracking
from ctxads.config import Settings, get_settings
from ctxads.context import AppContext
from ctxads.db.session import make_engine, make_session_factory
from ctxads.embeddings import build_embedder
from ctxads.jobs.scheduler import Scheduler
from ctxads.jobs.worker import Worker
from ctxads.llm import build_llm
from ctxads.max_api.client import MaxClient
from ctxads.miniapp.api import router as miniapp_router
from ctxads.transport import webhook
from ctxads.transport.polling import Poller

log = logging.getLogger(__name__)

Mode = Literal["polling", "webhook", "none"]

SHUTDOWN_TIMEOUT_SEC = 10


def build_context(settings: Settings) -> AppContext:
    engine = make_engine(settings.database_url)
    return AppContext(
        settings=settings,
        max=MaxClient(
            settings.max_api_base,
            settings.max_bot_token.get_secret_value(),
            rps=settings.max_rps,
            chat_rps=settings.max_chat_rps,
        ),
        sessions=make_session_factory(engine),
        llm=build_llm(settings),
        embedder=build_embedder(settings),
    )


def create_app(
    mode: Mode = "polling",
    settings: Settings | None = None,
    ctx: AppContext | None = None,
    *,
    background: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        context = ctx or build_context(settings)
        app.state.ctx = context
        tasks: list[asyncio.Task[None]] = []
        runners: list[Worker | Scheduler | Poller] = []
        if background:
            me = await context.max.get_me()
            context.bot_user_id = me.user_id  # кэшируем id бота: свои сообщения игнорируем
            log.info("bot @%s (id %s), mode=%s", me.username, me.user_id, mode)
            if mode == "webhook":
                await context.max.subscribe(
                    settings.webhook_public_url,
                    webhook.UPDATE_TYPES,
                    settings.webhook_secret.get_secret_value() or None,
                )
            runners = [Worker(context), Scheduler(context)]
            if mode == "polling":
                runners.append(Poller(context))
            tasks = [asyncio.create_task(r.run()) for r in runners]
        try:
            yield
        finally:
            # Сначала просим циклы остановиться сами (не рвём транзакции посередине),
            # и только зависшие (например, long polling в ожидании) отменяем.
            for r in runners:
                r.stop()
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_SEC)
                for t in pending:
                    t.cancel()
                    with suppress(asyncio.CancelledError):
                        await t
            if ctx is None:
                await context.max.aclose()

    app = FastAPI(title="ctxads", lifespan=lifespan, docs_url=None, redoc_url=None)
    if ctx is not None:
        app.state.ctx = ctx
    app.include_router(tracking.router)
    app.include_router(postback.router)
    app.include_router(showcase.router)
    app.include_router(miniapp_router)

    # Docker: /app/frontend/dist рядом с кодом. Локально (запуск из backend/): сборка лежит
    # в корне репозитория — backend/src/ctxads/app.py → parents[3] / frontend / dist.
    frontend_dist = Path.cwd() / "frontend" / "dist"
    if not frontend_dist.is_dir():
        frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount(
            "/miniapp",
            StaticFiles(directory=frontend_dist, html=True),
            name="miniapp",
        )
    if mode == "webhook":
        app.include_router(webhook.router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
