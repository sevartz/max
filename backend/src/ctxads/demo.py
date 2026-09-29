"""Демо-сценарий из docs/architecture.md §7 без реального MAX: uv run python -m ctxads.demo

MAX подменяется in-process фейком (httpx.MockTransport), всё остальное настоящее:
Postgres + pgvector, эмбеддер из EMBEDDER, LLM из LLM_PROVIDER (или --llm fake).
Каждый запуск создаёт новый демо-канал, реальные данные не трогает.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import random
import textwrap
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from ctxads.ads import stats as ad_stats
from ctxads.ads import tracking
from ctxads.bot.dispatcher import Dispatcher
from ctxads.config import get_settings
from ctxads.context import AppContext
from ctxads.db.models import Placement, Post
from ctxads.db.session import make_engine, make_session_factory
from ctxads.embeddings import build_embedder
from ctxads.jobs.worker import Worker
from ctxads.llm import build_llm
from ctxads.llm.fake import FakeLLM
from ctxads.max_api.client import MaxClient
from ctxads.max_api.models import Update
from ctxads.seed import seed

BOT_ID = 1
BASE = "https://max.demo"

POSTS = {
    "burger": (
        "Как бургер влияет на сахар в крови. После фастфуда уровень глюкозы резко растёт, "
        "а через час падает — отсюда усталость и снова голод. Если в семье был диабет, "
        "стоит регулярно проверять сахар."
    ),
    "sport": (
        "Как выбрать кроссовки для бега, если вы только начинаете. Для пробежек по асфальту "
        "нужна амортизация, для трейла — протектор. Тренировки три раза в неделю — лучший старт."
    ),
    "tragedy": (
        "Трагедия на трассе: в результате крупной аварии погибли пять человек, ещё десять "
        "пострадавших доставлены в больницы. Выражаем соболезнования родным и близким."
    ),
}
HISTORY = [
    "Правильное питание и сон — основа здоровья. Разбираем, сколько сахара в день норма.",
    "Тренировки для начинающих: как начать бегать и не бросить через неделю.",
    "Витамины осенью: что реально работает для иммунитета.",
]


class FakeMax:
    """Мини-MAX: принимает запросы бота, печатает то, что увидел бы пользователь."""

    def __init__(self, chat_id: int, owner_id: int) -> None:
        self.chat_id = chat_id
        self.owner_id = owner_id
        self.mids = itertools.count(1)
        self.last_admin_mid: str | None = None
        self.last_keyboard: list[list[dict[str, Any]]] = []
        self.channel_mids: list[str] = []
        self.views: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/me":
            return _json({"user_id": BOT_ID, "first_name": "ctxads", "is_bot": True})
        if path.endswith("/members/me"):
            perms = ["read_all_messages", "write", "delete"]
            return _json({"user_id": BOT_ID, "is_admin": True, "permissions": perms})
        if path.startswith("/chats/"):
            return _json(
                {
                    "chat_id": self.chat_id,
                    "type": "channel",
                    "title": "ЗОЖ без фанатизма",
                    "participants_count": 12000,
                }
            )
        if path == "/messages" and method == "GET":
            ids = request.url.params.get("message_ids")
            if ids:
                return _json(
                    {"messages": [self._msg(m, "", self.views.get(m, 0)) for m in ids.split(",")]}
                )
            return _json({"messages": [self._msg(f"old{i}", t) for i, t in enumerate(HISTORY)]})
        if path == "/messages" and method == "POST":
            return self._send(request, body)
        if path == "/messages" and method == "PUT":
            _print("✏️  бот правит сообщение админу", body.get("text", ""))
            self.last_keyboard = _buttons(body)
            return _json({"success": True})
        if path == "/answers":
            if body.get("notification"):
                print(f"   🔔 всплывашка: {body['notification']}")
            return _json({"success": True})
        return _json({"success": True})

    def _send(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        mid = f"mid.demo.{next(self.mids)}"
        if "chat_id" in request.url.params:
            _print("📣 бот публикует В КАНАЛ", body.get("text", ""), _buttons(body))
            self.channel_mids.append(mid)
            recipient = {"chat_id": self.chat_id, "chat_type": "channel"}
        else:
            _print("💬 бот пишет админу в личку", body.get("text", ""), _buttons(body))
            self.last_admin_mid = mid
            self.last_keyboard = _buttons(body)
            recipient = {"user_id": self.owner_id, "chat_type": "dialog"}
        return _json(
            {
                "message": {
                    "sender": {"user_id": BOT_ID, "is_bot": True},
                    "recipient": recipient,
                    "timestamp": 0,
                    "body": {"mid": mid, "text": body.get("text")},
                }
            }
        )

    def _msg(self, mid: str, text: str, views: int = 0) -> dict[str, Any]:
        return {
            "recipient": {"chat_id": self.chat_id, "chat_type": "channel"},
            "timestamp": 0,
            "body": {"mid": mid, "text": text},
            "stat": {"views": views},
        }

    def payload(self, prefix: str) -> str:
        for row in self.last_keyboard:
            for b in row:
                if b.get("payload", "").startswith(prefix) or b.get("payload", "").endswith(prefix):
                    return b["payload"]
        raise LookupError(prefix)


def _json(data: Any) -> httpx.Response:
    return httpx.Response(200, json=data)


def _buttons(body: dict[str, Any]) -> list[list[dict[str, Any]]]:
    for att in body.get("attachments") or []:
        if att.get("type") == "inline_keyboard":
            return att["payload"]["buttons"]
    return []


def _print(title: str, text: str, buttons: list[list[dict[str, Any]]] | None = None) -> None:
    print(f"\n{title}:")
    print(textwrap.indent(text, "   │ "))
    for row in buttons or []:
        print("   │ " + "  ".join(f"[{b['text']}]" for b in row))


def step(n: int, text: str) -> None:
    print(f"\n{'─' * 70}\n{n}. {text}")


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


async def run(llm_choice: str) -> None:
    settings = get_settings()
    chat_id = -random.randint(10**9, 2 * 10**9)
    owner = random.randint(10**6, 10**7)
    fake = FakeMax(chat_id, owner)
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    llm = FakeLLM() if llm_choice == "fake" else build_llm(settings)
    clock = Clock()
    ctx = AppContext(
        settings=settings,
        max=MaxClient(
            BASE,
            "demo",
            rps=0,
            chat_rps=0,
            http=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        ),
        sessions=sessions,
        llm=llm,
        embedder=build_embedder(settings),
        bot_user_id=BOT_ID,
        clock=clock,
    )
    print(f"LLM: {llm.name} · эмбеддер: {settings.embedder} · канал {chat_id}")
    n = await seed(sessions, ctx.embedder)
    print(f"Каталог: {n} объявлений")
    d = Dispatcher(ctx)
    user = {"user_id": owner, "first_name": "Анна", "is_bot": False}
    ts = itertools.count(int(clock.now.timestamp() * 1000))

    def cb(payload: str) -> Update:
        return Update.model_validate(
            {
                "update_type": "message_callback",
                "timestamp": next(ts),
                "callback": {"callback_id": f"cb{next(ts)}", "payload": payload, "user": user},
                "message": {
                    "recipient": {"user_id": owner, "chat_type": "dialog"},
                    "body": {"mid": fake.last_admin_mid or "x"},
                },
            }
        )

    def post(key: str) -> Update:
        return Update.model_validate(
            {
                "update_type": "message_created",
                "timestamp": next(ts),
                "message": {
                    "recipient": {"chat_id": chat_id, "chat_type": "channel"},
                    "timestamp": next(ts),
                    "body": {"mid": f"mid.post.{key}.{chat_id}", "text": POSTS[key]},
                },
            }
        )

    step(1, "Админ: /start → «Принимаю» → добавляет бота в канал")
    await d.dispatch(
        Update.model_validate({"update_type": "bot_started", "timestamp": next(ts), "user": user})
    )
    await d.dispatch(cb("c:accept"))
    await d.dispatch(
        Update.model_validate(
            {
                "update_type": "bot_added",
                "timestamp": next(ts),
                "chat_id": chat_id,
                "user": user,
                "is_channel": True,
            }
        )
    )
    step(1, "Админ в /settings разрешает регулируемую категорию «Медицина»")
    await d.dispatch(cb(f"s:{chat_id}:r:medicine"))

    step(2, "В канале выходит пост «Как бургер влияет на сахар в крови»")
    await d.dispatch(post("burger"))

    step(3, "Админ жмёт «🚫 Не предлагать Медицину»")
    await d.dispatch(cb(fake.payload(":block_cat")))

    # Пост о трагедии — пока нет открытого предложения: иначе частотное правило отсеет его
    # ещё до LLM, и brand safety не будет виден.
    step(4, "Пост о трагедии")
    await d.dispatch(post("tragedy"))
    async with sessions() as s:
        p = await s.scalar(select(Post).where(Post.mid == f"mid.post.tragedy.{chat_id}"))
    print(
        f"\n   🛡  предложений нет: skip_reason={p.skip_reason}, "
        f"brand_safety={(p.analysis or {}).get('brand_safety')}"
    )

    step(5, "Следующий пост — про бег")
    await d.dispatch(post("sport"))

    step(6, "Админ одобряет рекламу к посту про бег; через минуту воркер публикует")
    await d.dispatch(cb(fake.payload(":approve")))
    clock.now += timedelta(minutes=1)
    await Worker(ctx).drain()

    step(7, "Читатель кликает «Подробнее», пост набирает 1500 просмотров, админ смотрит /stats")
    async with sessions() as s:
        placement = await s.scalar(select(Placement).where(Placement.chat_id == chat_id))
    url, _ = await tracking.track_click(ctx, placement.token, "203.0.113.7", "demo-UA")
    print(f"\n   🔗 {settings.public_base_url}/r/{placement.token} → 302 {url}")
    fake.views[placement.mid] = 1500
    await ad_stats.poll_views(ctx)
    await d.dispatch(
        Update.model_validate(
            {
                "update_type": "message_created",
                "timestamp": next(ts),
                "message": {
                    "sender": user,
                    "recipient": {"user_id": BOT_ID, "chat_type": "dialog"},
                    "body": {"mid": f"cmd{next(ts)}", "text": "/stats"},
                },
            }
        )
    )
    await ctx.max.aclose()
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm",
        choices=["env", "fake"],
        default="fake",
        help="env — провайдер из .env (NIM тратит ~6 кредитов), fake — офлайн",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args.llm))


if __name__ == "__main__":
    main()
