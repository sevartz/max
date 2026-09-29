from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from ctxads.max_api.client import MaxClient, max_ssl_context
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Update
from ctxads.ratelimit import KeyedRateLimiter
from tests.conftest import load_update

BASE = "https://max.test"


@pytest.fixture
async def client():
    c = MaxClient(BASE, "tok", rps=0, chat_rps=0, backoff_base=0)
    yield c
    await c.aclose()


@respx.mock(base_url=BASE)
async def test_auth_header_and_send(respx_mock, client):
    route = respx_mock.post("/messages").respond(
        json={
            "message": {
                "recipient": {"chat_id": 1, "chat_type": "channel"},
                "body": {"mid": "m1", "text": "hi"},
            }
        }
    )
    msg = await client.send_message(chat_id=1, text="hi")
    req = route.calls.last.request
    assert req.headers["Authorization"] == "tok"
    assert "access_token" not in str(req.url), "токен только в заголовке"
    assert req.url.params["chat_id"] == "1"
    assert json.loads(req.content) == {"text": "hi"}
    assert msg is not None and msg.mid == "m1"


@respx.mock(base_url=BASE)
async def test_retries_on_429_then_succeeds(respx_mock, client):
    route = respx_mock.get("/me").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json={"user_id": 7, "is_bot": True}),
        ]
    )
    me = await client.get_me()
    assert me.user_id == 7 and route.call_count == 3


@respx.mock(base_url=BASE)
async def test_no_retry_on_4xx(respx_mock, client):
    route = respx_mock.get("/chats/5").respond(403, json={"code": "access.denied", "message": "no"})
    with pytest.raises(MaxApiError) as e:
        await client.get_chat(5)
    assert e.value.status == 403 and e.value.code == "access.denied"
    assert route.call_count == 1


@respx.mock(base_url=BASE)
async def test_answer_callback_body(respx_mock, client):
    route = respx_mock.post("/answers").respond(json={"success": True})
    await client.answer_callback("cb1", notification="ok")
    req = route.calls.last.request
    assert req.url.params["callback_id"] == "cb1"
    assert json.loads(req.content) == {"notification": "ok"}


async def test_send_requires_one_recipient(client):
    with pytest.raises(ValueError):
        await client.send_message(text="x")


def test_update_models_tolerate_unknown_fields():
    raw = load_update("message_callback")
    raw["brand_new_field"] = {"x": 1}
    upd = Update.model_validate(raw)
    assert upd.callback.payload == "c:accept"
    assert upd.dedupe_key() == "message_callback:5001:cb-1:1790000002000"


async def test_per_chat_rate_limit():
    limiter = KeyedRateLimiter(2, period=0.2)
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire("chat")
    assert time.monotonic() - start >= 0.19
    start = time.monotonic()
    await limiter.acquire("other")
    assert time.monotonic() - start < 0.05, "лимит отдельный на каждый чат"


def test_ssl_context_trusts_russian_root_ca_only_for_max():
    names = [
        dict(rdn[0] for rdn in cert["subject"]).get("commonName")
        for cert in max_ssl_context().get_ca_certs()
    ]
    assert "Russian Trusted Root CA" in names
    assert len(names) > 1, "certifi тоже загружен, а не только корень Минцифры"
