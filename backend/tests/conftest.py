from __future__ import annotations

import copy
import itertools
import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ctxads.config import Settings
from ctxads.context import AppContext
from ctxads.db.models import Base
from ctxads.db.session import make_session_factory
from ctxads.embeddings.fake import FakeEmbedder
from ctxads.llm.fake import FakeLLM
from ctxads.max_api.client import MaxClient
from ctxads.max_api.models import Update

FIXTURES = Path(__file__).parent / "fixtures" / "updates"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://ctx:ctx@localhost:5432/ctx_test"
)
MAX_BASE = "https://max.test"
BOT_ID = 900
OWNER_ID = 101
CHANNEL_ID = -7001


def load_update(name: str, **overrides: Any) -> dict[str, Any]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


def channel_post(mid: str, text_: str, ts: int = 1790000004000) -> Update:
    raw = load_update("message_created_channel")
    raw["message"]["body"]["mid"] = mid
    raw["message"]["body"]["text"] = text_
    raw["timestamp"] = raw["message"]["timestamp"] = ts
    return Update.model_validate(raw)


def callback(payload: str, cb_id: str = "cb-x", user_id: int = OWNER_ID) -> Update:
    raw = copy.deepcopy(load_update("message_callback"))
    raw["callback"]["payload"] = payload
    raw["callback"]["callback_id"] = cb_id
    raw["callback"]["user"]["user_id"] = user_id
    return Update.model_validate(raw)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=TEST_DB,
        max_bot_token="test-token",  # type: ignore[arg-type]
        max_api_base=MAX_BASE,
        max_rps=0,
        max_chat_rps=0,
        webhook_secret="s3cret",  # type: ignore[arg-type]
        public_base_url="https://ads.test",
        llm_provider="fake",
        embedder="fake",
        click_salt="salt",  # type: ignore[arg-type]
    )


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url)
    try:
        async with eng.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except OSError as e:
        pytest.skip(f"PostgreSQL недоступен ({TEST_DB}): {e}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncEngine:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return engine


class MaxMock:
    """respx-мок MAX Bot API с разумными ответами по умолчанию."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.permissions = ["read_all_messages", "write", "delete"]
        self.participants = 12000
        self.channel_posts: list[dict[str, Any]] = [
            {
                "recipient": {"chat_id": CHANNEL_ID, "chat_type": "channel"},
                "timestamp": 1,
                "body": {"mid": f"mid.old.{i}", "text": t},
            }
            for i, t in enumerate(
                [
                    "Правильное питание и сон — основа здоровья. "
                    "Разбираем, сколько сахара в день норма.",
                    "Тренировки для начинающих: как начать бегать и не бросить через неделю.",
                    "Витамины осенью: что реально работает для иммунитета.",
                ]
            )
        ]
        self.views: dict[str, int] = {}
        self._mids = itertools.count(1)
        router.get("/me").respond(
            json={
                "user_id": BOT_ID,
                "first_name": "ctxads",
                "is_bot": True,
                "username": "ctxads_bot",
            }
        )
        router.post("/messages").mock(side_effect=self._send)
        router.put("/messages").respond(json={"success": True})
        router.delete("/messages").respond(json={"success": True})
        router.post("/answers").respond(json={"success": True})
        router.post("/subscriptions").respond(json={"success": True})
        router.get(url__regex=r"/chats/-?\d+/members/me$").mock(side_effect=self._me)
        router.get(url__regex=r"/chats/-?\d+$").mock(side_effect=self._chat)
        router.get("/messages").mock(side_effect=self._messages)
        self.updates: list[dict[str, Any]] = []
        router.get("/updates").mock(side_effect=self._updates)

    def _send(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = request.url.params
        recipient: dict[str, Any] = {}
        if "chat_id" in params:
            recipient = {"chat_id": int(params["chat_id"]), "chat_type": "channel"}
        else:
            recipient = {"user_id": int(params["user_id"]), "chat_type": "dialog"}
        mid = f"mid.sent.{next(self._mids)}"
        return httpx.Response(
            200,
            json={
                "message": {
                    "sender": {"user_id": BOT_ID, "is_bot": True, "first_name": "ctxads"},
                    "recipient": recipient,
                    "timestamp": 1790000000000,
                    "body": {"mid": mid, "text": body.get("text")},
                }
            },
        )

    def _updates(self, request: httpx.Request) -> httpx.Response:
        batch, self.updates = self.updates, []
        return httpx.Response(200, json={"updates": batch, "marker": 42})

    def _me(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "user_id": BOT_ID,
                "is_admin": True,
                "is_bot": True,
                "permissions": self.permissions,
            },
        )

    def _chat(self, request: httpx.Request) -> httpx.Response:
        chat_id = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(
            200,
            json={
                "chat_id": chat_id,
                "type": "channel",
                "status": "active",
                "title": "ЗОЖ без фанатизма",
                "participants_count": self.participants,
            },
        )

    def _messages(self, request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get("message_ids")
        if ids:
            msgs = [
                {
                    "recipient": {"chat_id": CHANNEL_ID, "chat_type": "channel"},
                    "timestamp": 1,
                    "body": {"mid": mid, "text": "ad"},
                    "stat": {"views": self.views.get(mid, 0)},
                }
                for mid in ids.split(",")
            ]
            return httpx.Response(200, json={"messages": msgs})
        return httpx.Response(200, json={"messages": self.channel_posts})

    # --- удобства для проверок ---

    def sent(self, *, user_id: int | None = None, chat_id: int | None = None) -> list[dict]:
        out = []
        for call in self.router.calls:
            req = call.request
            if req.method != "POST" or req.url.path != "/messages":
                continue
            params = req.url.params
            if user_id is not None and params.get("user_id") != str(user_id):
                continue
            if chat_id is not None and params.get("chat_id") != str(chat_id):
                continue
            out.append(json.loads(req.content))
        return out

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [
            c.request
            for c in self.router.calls
            if c.request.method == method and c.request.url.path == path
        ]

    def edits(self) -> list[dict]:
        return [json.loads(r.content) for r in self.calls("PUT", "/messages")]


@pytest.fixture
def max_mock() -> Iterator[MaxMock]:
    with respx.mock(base_url=MAX_BASE, assert_all_called=False) as router:
        yield MaxMock(router)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def ctx(
    settings: Settings, db: AsyncEngine, max_mock: MaxMock, clock: Clock, llm: FakeLLM
) -> AsyncIterator[AppContext]:
    client = MaxClient(MAX_BASE, "test-token", rps=0, chat_rps=0, backoff_base=0)
    context = AppContext(
        settings=settings,
        max=client,
        sessions=make_session_factory(db),
        llm=llm,
        embedder=FakeEmbedder(),
        bot_user_id=BOT_ID,
        clock=clock,
    )
    yield context
    await client.aclose()


@pytest.fixture
async def seeded(ctx: AppContext) -> AppContext:
    from ctxads.seed import seed

    await seed(ctx.sessions, ctx.embedder)
    return ctx
