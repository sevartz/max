from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus

import httpx
import pytest
from sqlalchemy import select

from ctxads.ads import cabinet
from ctxads.app import create_app
from ctxads.db.models import Ad, ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import users as users_repo
from tests.conftest import OWNER_ID


def signed_data(token: str, user_id: int) -> str:
    values = {
        "auth_date": "1790326800",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return (
        "&".join(f"{key}={quote_plus(value)}" for key, value in values.items()) + f"&hash={digest}"
    )


async def _consent(ctx, user_id: int) -> None:
    async with ctx.sessions.begin() as s:
        await users_repo.upsert_user(s, user_id, "Тест")
        await users_repo.add_consent(s, user_id, ctx.settings.consent_version)


# Как в app.py: сборка рядом (Docker) или в корне репозитория при запуске из backend/.
REPO_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


async def _client(ctx):
    app = create_app("polling", ctx.settings, ctx, background=False)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://app")


async def test_miniapp_static_build_is_served(settings):
    if not ((Path.cwd() / "frontend" / "dist").is_dir() or REPO_FRONTEND_DIST.is_dir()):
        pytest.skip("frontend has not been built")
    app = create_app("polling", settings, background=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        response = await client.get("/miniapp/")
    assert response.status_code == 200
    assert 'id="root"' in response.text


async def test_miniapp_rejects_bad_auth_and_requires_consent(seeded):
    async with await _client(seeded) as client:
        bad = await client.get(
            "/api/miniapp/bootstrap",
            headers={"X-Max-Init-Data": "user=bad&hash=" + "0" * 64},
        )
        assert bad.status_code == 401
        fresh = signed_data(seeded.settings.max_bot_token.get_secret_value(), OWNER_ID)
        need_consent = await client.get(
            "/api/miniapp/channels/-7001", headers={"X-Max-Init-Data": fresh}
        )
        assert need_consent.status_code == 403


async def test_miniapp_checks_channel_ownership(seeded):
    await _consent(seeded, OWNER_ID)
    await _consent(seeded, OWNER_ID + 1)
    async with seeded.sessions.begin() as s:
        await channels_repo.upsert(
            s, -7001, OWNER_ID, status=ChannelStatus.ACTIVE, title="Мой канал"
        )
    token = seeded.settings.max_bot_token.get_secret_value()
    async with await _client(seeded) as client:
        response = await client.get(
            "/api/miniapp/channels/-7001",
            headers={"X-Max-Init-Data": signed_data(token, OWNER_ID + 1)},
        )
    assert response.status_code == 404


async def test_miniapp_idempotency_prevents_duplicate_demo_topup(seeded):
    await _consent(seeded, OWNER_ID)
    async with seeded.sessions.begin() as s:
        advertiser = await cabinet.create_advertiser(s, OWNER_ID, "Тестовая компания")
        ad = await cabinet.create_ad(
            s,
            seeded.embedder,
            advertiser,
            {
                "title": "Тестовое объявление",
                "body": "Описание тестового объявления",
                "url": "https://example.com",
                "category": "food",
                "pricing_model": "cpm",
                "price": "100",
                "budget": "500",
            },
        )
        ad_id = ad.id

    token = seeded.settings.max_bot_token.get_secret_value()
    headers = {
        "X-Max-Init-Data": signed_data(token, OWNER_ID),
        "Idempotency-Key": "retryable-topup-key-123",
    }
    async with await _client(seeded) as client:
        first = await client.post(
            f"/api/miniapp/ads/{ad_id}/topup", json={"amount": "100"}, headers=headers
        )
        second = await client.post(
            f"/api/miniapp/ads/{ad_id}/topup", json={"amount": "100"}, headers=headers
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["budget_total"] == second.json()["budget_total"] == "600.00"
    async with seeded.sessions() as s:
        saved = await s.scalar(select(Ad).where(Ad.id == ad_id))
    assert saved is not None and saved.budget_total == Decimal("600.00")
