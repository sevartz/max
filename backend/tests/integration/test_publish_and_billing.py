from __future__ import annotations

from decimal import Decimal

import httpx
from sqlalchemy import func, select

from ctxads.ads import postback, tracking
from ctxads.ads.stats import poll_views
from ctxads.app import create_app
from ctxads.db.models import (
    Ad,
    Click,
    Job,
    LedgerEntry,
    Placement,
    Proposal,
    ProposalStatus,
)
from ctxads.jobs import tasks
from ctxads.jobs.worker import Worker
from ctxads.transport.ingest import ingest
from tests.conftest import CHANNEL_ID, OWNER_ID, callback, channel_post, load_update
from tests.integration.test_matching_flow import BURGER, connect


async def _proposal(ctx) -> Proposal:
    async with ctx.sessions() as s:
        return await s.scalar(select(Proposal).order_by(Proposal.id.desc()))


async def _approve_and_publish(ctx, max_mock, clock) -> Placement:
    d = await connect(ctx)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    p = await _proposal(ctx)
    await d.dispatch(callback(f"p:{p.id}:approve", "cb-approve"))

    assert "Одобрено, выйдет в" in max_mock.edits()[-1]["text"]
    worker = Worker(ctx)
    assert await worker.drain() == 0, "публикация отложена на publish_delay"
    clock.advance(minutes=1)
    assert await worker.drain() == 1
    async with ctx.sessions() as s:
        return await s.scalar(select(Placement))


async def test_approve_publishes_marked_ad(seeded, max_mock, clock):
    placement = await _approve_and_publish(seeded, max_mock, clock)

    [post] = max_mock.sent(chat_id=CHANNEL_ID)
    assert "#Реклама. ООО «ЛабТест», ИНН 7700000001. erid: DEMO-2VtzqwLAB1" in post["text"]
    [button] = post["attachments"][-1]["payload"]["buttons"][0]
    assert button["type"] == "link"
    assert button["url"] == f"https://ads.test/r/{placement.token}"
    assert placement.mid and placement.published_at
    assert (await _proposal(seeded)).status == ProposalStatus.PUBLISHED
    assert "опубликована" in max_mock.sent(user_id=OWNER_ID)[-1]["text"]
    async with seeded.sessions() as s:
        delete_job = await s.scalar(select(Job).where(Job.kind == "delete_ad"))
    assert delete_job is not None, "есть право delete → автоудаление через ad_ttl_hours"


async def test_publish_rechecks_rights(seeded, max_mock, clock):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    p = await _proposal(seeded)
    await d.dispatch(callback(f"p:{p.id}:approve"))
    max_mock.permissions = ["read_all_messages"]
    clock.advance(minutes=11)
    await Worker(seeded).drain()
    assert max_mock.sent(chat_id=CHANNEL_ID) == []
    assert (await _proposal(seeded)).status == ProposalStatus.CANCELLED
    assert "Не удалось опубликовать" in max_mock.sent(user_id=OWNER_ID)[-1]["text"]


async def test_click_redirect_and_cpm_billing(seeded, max_mock, clock):
    placement = await _approve_and_publish(seeded, max_mock, clock)

    url, click_id = await tracking.track_click(seeded, placement.token, "1.2.3.4", "UA")
    assert url.startswith("https://example.com/labtest/glucose?")
    assert "utm_source=max" in url and f"click_id={click_id}" in url
    await tracking.track_click(seeded, placement.token, "1.2.3.4", "UA")  # дубль за 24 ч
    async with seeded.sessions() as s:
        clicks = (await s.scalars(select(Click))).all()
    assert [c.is_unique for c in clicks] == [True, False]
    assert all(c.ip_hash != "1.2.3.4" for c in clicks), "IP только в виде соль+хэш"

    # CPM 250 ₽: +2000 просмотров → 500 ₽, из них 70% каналу.
    max_mock.views[placement.mid] = 2000
    assert await poll_views(seeded) == 1
    assert await poll_views(seeded) == 0, "без прироста — без начислений"
    async with seeded.sessions() as s:
        entry = await s.scalar(select(LedgerEntry))
        ad = await s.get(Ad, entry.ad_id)
    assert entry.kind == "cpm"
    assert entry.advertiser_debit == Decimal("500.00")
    assert entry.channel_credit == Decimal("350.00")
    assert entry.platform_fee == Decimal("150.00")
    assert ad.budget_left == Decimal("19500.00")

    from ctxads.bot.handlers import stats

    await stats.show(seeded, OWNER_ID)
    text = max_mock.sent(user_id=OWNER_ID)[-1]["text"]
    assert "размещений 1, просмотров 2000, кликов 1, заработано 350 ₽" in text


async def test_cpc_budget_exhaustion_pauses_ad(seeded, max_mock, clock):
    placement = await _approve_and_publish(seeded, max_mock, clock)
    async with seeded.sessions.begin() as s:
        ad = await s.get(Ad, placement.ad_id)
        ad.pricing_model, ad.price, ad.budget_left = "cpc", Decimal("30"), Decimal("45")
    await tracking.track_click(seeded, placement.token, "1.1.1.1", "UA")
    await tracking.track_click(seeded, placement.token, "2.2.2.2", "UA")
    async with seeded.sessions() as s:
        ad = await s.get(Ad, placement.ad_id)
        total = await s.scalar(select(func.sum(LedgerEntry.advertiser_debit)))
    assert total == Decimal("45.00"), "не списываем больше остатка бюджета"
    assert ad.budget_left == 0 and ad.status == "paused"


async def test_cpa_postback_with_signature(seeded, max_mock, clock):
    placement = await _approve_and_publish(seeded, max_mock, clock)
    async with seeded.sessions.begin() as s:
        ad = await s.get(Ad, placement.ad_id)
        ad.pricing_model, ad.price = "cpa", Decimal("150")
    _, click_id = await tracking.track_click(seeded, placement.token, "1.1.1.1", "UA")

    bad = postback.PostbackError
    try:
        await postback.record_conversion(seeded, click_id, "1000", "wrong")
        raise AssertionError("подпись не проверена")
    except bad as e:
        assert e.status == 403
    sig = postback.sign("demo-labtest-secret", click_id, "1000")
    assert await postback.record_conversion(seeded, click_id, "1000", sig) == Decimal("150.00")
    assert await postback.record_conversion(seeded, click_id, "1000", sig) == 0, "идемпотентно"


async def test_proposal_expires(seeded, max_mock, clock):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    clock.advance(minutes=seeded.settings.proposal_ttl_min + 1)
    await tasks.expire_proposals(seeded, {})
    assert (await _proposal(seeded)).status == ProposalStatus.EXPIRED
    edit = max_mock.edits()[-1]
    assert "истекло" in edit["text"] and edit["attachments"] == []

    # Нажатие после истечения — «неактуально», ничего не публикуем.
    p = await _proposal(seeded)
    await d.dispatch(callback(f"p:{p.id}:approve"))
    assert (await _proposal(seeded)).status == ProposalStatus.EXPIRED


async def test_frequency_after_publication(seeded, max_mock, clock):
    await _approve_and_publish(seeded, max_mock, clock)
    d = await connect(seeded)
    for i in range(3):
        clock.advance(minutes=5)
        await d.dispatch(channel_post(f"mid.next.{i}", BURGER + f" Часть {i}."))
    async with seeded.sessions() as s:
        n = await s.scalar(select(func.count()).select_from(Proposal))
    assert n == 1, "min_minutes_between_ads=120 ещё не прошло"


async def test_ingest_deduplicates(seeded):
    raw = load_update("message_created_channel")
    assert await ingest(seeded.sessions, raw) is True
    assert await ingest(seeded.sessions, raw) is False
    async with seeded.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 1


async def test_webhook_checks_secret_and_enqueues(seeded):
    app = create_app("webhook", seeded.settings, seeded, background=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        raw = load_update("bot_started")
        r = await client.post("/webhook/max", json=raw, headers={"X-Max-Bot-Api-Secret": "bad"})
        assert r.status_code == 401
        r = await client.post("/webhook/max", json=raw, headers={"X-Max-Bot-Api-Secret": "s3cret"})
        assert r.status_code == 200
        r = await client.post("/webhook/max", json=raw, headers={"X-Max-Bot-Api-Secret": "s3cret"})
        assert r.status_code == 200
        assert (await client.get("/healthz")).json() == {"status": "ok"}
    async with seeded.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 1


async def test_worker_runs_update_job_end_to_end(seeded, max_mock):
    await ingest(seeded.sessions, load_update("bot_started"))
    assert await Worker(seeded).drain() == 1
    assert max_mock.sent(user_id=OWNER_ID), "бот ответил на /start через очередь"


async def test_tracking_endpoint_redirects(seeded, max_mock, clock):
    placement = await _approve_and_publish(seeded, max_mock, clock)
    app = create_app("polling", seeded.settings, seeded, background=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        r = await client.get(f"/r/{placement.token}")
        assert r.status_code == 302
        assert r.headers["location"].startswith("https://example.com/labtest/glucose")
        assert (await client.get("/r/unknown")).status_code == 404
        page = await client.get("/showcase")
        assert page.status_code == 200 and "ЛабТест" in page.text
