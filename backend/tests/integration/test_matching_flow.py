from __future__ import annotations

import pytest
from sqlalchemy import select

from ctxads.bot.dispatcher import Dispatcher
from ctxads.db.models import Post, Proposal, ProposalStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.llm.base import LLMUnavailable
from ctxads.matching.policy import Skip
from ctxads.max_api.models import Update
from tests.conftest import CHANNEL_ID, OWNER_ID, callback, channel_post, load_update

BURGER = load_update("message_created_channel")["message"]["body"]["text"]
SPORT = (
    "Как выбрать кроссовки для бега, если вы только начинаете. Для пробежек по асфальту нужна "
    "амортизация, для трейла — протектор. Тренировки три раза в неделю — лучший старт."
)
TRAGEDY = (
    "Трагедия на трассе: в результате крупной аварии погибли пять человек, ещё десять "
    "пострадавших доставлены в больницы. Выражаем соболезнования родным и близким погибших."
)


async def connect(ctx, *, allow_regulated=("medicine",)) -> Dispatcher:
    d = Dispatcher(ctx)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))
    async with ctx.sessions.begin() as s:
        cs = await channels_repo.get_settings(s, CHANNEL_ID)
        cs.allow_regulated = list(allow_regulated)
    return d


async def proposals(ctx) -> list[Proposal]:
    async with ctx.sessions() as s:
        return list((await s.scalars(select(Proposal).order_by(Proposal.id))).all())


async def post_by_mid(ctx, mid: str) -> Post:
    async with ctx.sessions() as s:
        return await s.scalar(select(Post).where(Post.mid == mid))


async def test_post_gets_contextual_proposal(seeded, max_mock):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))

    [p] = await proposals(seeded)
    assert p.status == ProposalStatus.PENDING
    assert p.score >= seeded.settings.min_match_score
    msg = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "ЛабТест — анализ крови на глюкозу" in msg["text"]
    assert "🧠 Почему:" in msg["text"] and "CPM" in msg["text"]
    buttons = [b["payload"] for row in msg["attachments"][0]["payload"]["buttons"] for b in row]
    assert buttons == [
        f"p:{p.id}:approve",
        f"p:{p.id}:reject",
        f"p:{p.id}:next",
        f"p:{p.id}:block_cat",
    ]
    assert p.admin_message_mid is not None
    assert p.candidates, "оставшиеся кандидаты сохранены для «Другой вариант»"


async def test_regulated_category_needs_explicit_allow(seeded, max_mock):
    d = await connect(seeded, allow_regulated=())
    await d.dispatch(channel_post("mid.post.1", BURGER))
    ps = await proposals(seeded)
    assert all("ЛабТест" not in m["text"] for m in max_mock.sent(user_id=OWNER_ID))
    async with seeded.sessions() as s:
        from ctxads.db.models import Ad

        ads = {a.id: a for a in (await s.scalars(select(Ad))).all()}
    assert all(ads[p.ad_id].category != "medicine" for p in ps)


async def test_demo_block_category_then_sport(seeded, max_mock):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    [p] = await proposals(seeded)

    await d.dispatch(callback(f"p:{p.id}:block_cat", "cb-block"))
    async with seeded.sessions() as s:
        cs = await channels_repo.get_settings(s, CHANNEL_ID)
    assert "medicine" in cs.blocked_categories
    [p] = await proposals(seeded)
    assert p.status == ProposalStatus.REJECTED
    assert "больше не предлагается" in max_mock.edits()[-1]["text"]

    await d.dispatch(channel_post("mid.post.2", SPORT, ts=1790000100000))
    ps = await proposals(seeded)
    assert len(ps) == 2
    assert "Бегун" in max_mock.sent(user_id=OWNER_ID)[-1]["text"]
    assert "ЛабТест" not in max_mock.sent(user_id=OWNER_ID)[-1]["text"]


async def test_tragedy_post_gets_no_ads(seeded, max_mock):
    d = await connect(seeded)
    before = len(max_mock.sent(user_id=OWNER_ID))
    await d.dispatch(channel_post("mid.post.t", TRAGEDY))

    assert await proposals(seeded) == []
    assert len(max_mock.sent(user_id=OWNER_ID)) == before
    post = await post_by_mid(seeded, "mid.post.t")
    assert post.skip_reason == Skip.UNSAFE
    assert post.analysis["brand_safety"] == "unsafe"


async def test_short_post_skipped_without_llm(seeded, max_mock, llm):
    d = await connect(seeded)
    calls = len(llm.calls)
    await d.dispatch(channel_post("mid.short", "Фото дня 🌿"))
    assert len(llm.calls) == calls
    assert (await post_by_mid(seeded, "mid.short")).skip_reason == Skip.TOO_SHORT


async def test_open_proposal_blocks_next_post(seeded, max_mock):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    await d.dispatch(channel_post("mid.post.2", SPORT))
    assert len(await proposals(seeded)) == 1
    assert (await post_by_mid(seeded, "mid.post.2")).skip_reason == Skip.OPEN_PROPOSAL


async def test_next_gives_another_ad_and_limits(seeded, max_mock):
    # Порог 0 — чтобы кандидатов гарантированно хватило и проверялся именно лимит.
    seeded.settings = seeded.settings.model_copy(update={"min_match_score": 0.0})
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    seen = set()
    for i in range(5):
        current = [p for p in await proposals(seeded) if p.status == ProposalStatus.PENDING]
        assert len(current) == 1
        seen.add(current[0].ad_id)
        await d.dispatch(callback(f"p:{current[0].id}:next", f"cb-next-{i}"))
    ps = await proposals(seeded)
    assert len(ps) == 4, "исходное + не больше 3 «других вариантов»"
    assert len(seen) == 4
    assert [p.status for p in ps[:3]] == [ProposalStatus.SUPERSEDED] * 3
    assert "Других подходящих вариантов" in max_mock.edits()[-1]["text"]


async def test_analysis_cached_by_text_hash(seeded, max_mock, llm):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    [p] = await proposals(seeded)
    await d.dispatch(callback(f"p:{p.id}:reject"))
    analyze_calls = sum(1 for c in llm.calls if "brand_safety" in c["system"])
    await d.dispatch(channel_post("mid.post.repost", BURGER))
    assert sum(1 for c in llm.calls if "brand_safety" in c["system"]) == analyze_calls


async def test_llm_unavailable_skips_post(seeded, max_mock, llm, monkeypatch):
    d = await connect(seeded)

    async def boom(*a, **kw):
        raise LLMUnavailable("down")

    monkeypatch.setattr(llm, "chat", boom)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    assert await proposals(seeded) == []
    assert (await post_by_mid(seeded, "mid.post.1")).skip_reason == Skip.LLM_UNAVAILABLE


async def test_invalid_llm_json_retried_once(seeded, max_mock, llm):
    d = await connect(seeded)
    llm.responses = [
        "не JSON",
        '{"summary": "пост про сахар в крови и анализы", '
        '"topics": ["сахар", "анализ крови"], "category": "health", '
        '"intent": "информационный", "brand_safety": "safe", "unsafe_reason": null}',
    ]
    await d.dispatch(channel_post("mid.post.1", BURGER))
    post = await post_by_mid(seeded, "mid.post.1")
    assert post.analysis["category"] == "health"


async def test_own_messages_and_inactive_channels_ignored(seeded, max_mock):
    d = await connect(seeded)
    raw = channel_post("mid.bot", BURGER).model_dump(mode="json")
    raw["message"]["sender"] = {"user_id": 900, "is_bot": True}
    await d.dispatch(Update.model_validate(raw))
    assert await post_by_mid(seeded, "mid.bot") is None

    ad_text = BURGER + "\n\nРеклама. ООО «Тест», ИНН 7700000000. erid: DEMO-1"
    await d.dispatch(channel_post("mid.ad", ad_text))
    post = await post_by_mid(seeded, "mid.ad")
    assert post.is_our_ad and await proposals(seeded) == []

    raw = channel_post("mid.other", BURGER).model_dump(mode="json")
    raw["message"]["recipient"]["chat_id"] = -9999
    await d.dispatch(Update.model_validate(raw))
    assert await post_by_mid(seeded, "mid.other") is None


@pytest.mark.parametrize("user_id", [555])
async def test_only_owner_can_decide(seeded, max_mock, user_id):
    d = await connect(seeded)
    await d.dispatch(channel_post("mid.post.1", BURGER))
    [p] = await proposals(seeded)
    await d.dispatch(callback(f"p:{p.id}:approve", "cb-stranger", user_id=user_id))
    [p] = await proposals(seeded)
    assert p.status == ProposalStatus.PENDING
    answer = max_mock.calls("POST", "/answers")[-1]
    assert "владелец" in answer.content.decode()
