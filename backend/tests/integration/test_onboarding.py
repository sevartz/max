from __future__ import annotations

from ctxads.bot import texts
from ctxads.bot.dispatcher import Dispatcher
from ctxads.db.models import ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import users as users_repo
from ctxads.max_api.models import Update
from tests.conftest import CHANNEL_ID, OWNER_ID, callback, load_update


async def test_start_sends_consent_with_button(ctx, max_mock):
    await Dispatcher(ctx).dispatch(Update.model_validate(load_update("message_created_dialog")))

    [msg] = max_mock.sent(user_id=OWNER_ID)
    assert msg["text"] == texts.CONSENT
    buttons = msg["attachments"][0]["payload"]["buttons"]
    assert buttons == [[{"type": "callback", "text": texts.CONSENT_BUTTON, "payload": "c:accept"}]]


async def test_consent_callback_stores_consent(ctx, max_mock):
    d = Dispatcher(ctx)
    await d.dispatch(Update.model_validate(load_update("bot_started")))
    await d.dispatch(callback("c:accept", "cb-consent"))

    async with ctx.sessions() as s:
        assert await users_repo.has_consent(s, OWNER_ID, ctx.settings.consent_version)
    [answer] = max_mock.calls("POST", "/answers")
    assert answer.url.params["callback_id"] == "cb-consent"
    assert max_mock.sent(user_id=OWNER_ID)[-1]["text"] == texts.CONSENT_ACCEPTED

    # Повторный /start — «уже приняты», без кнопки.
    await d.dispatch(Update.model_validate(load_update("bot_started")))
    assert max_mock.sent(user_id=OWNER_ID)[-1]["text"] == texts.CONSENT_ALREADY


async def test_bot_added_without_consent_waits(ctx, max_mock):
    await Dispatcher(ctx).dispatch(Update.model_validate(load_update("bot_added")))

    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch is not None and ch.status == ChannelStatus.PENDING_CONSENT
    assert "принять условия" in max_mock.sent(user_id=OWNER_ID)[0]["text"]

    # После согласия канал подключается сам.
    await Dispatcher(ctx).dispatch(callback("c:accept"))
    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch.status == ChannelStatus.ACTIVE


async def test_bot_added_connects_channel_and_builds_profile(ctx, max_mock, llm):
    d = Dispatcher(ctx)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))

    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch.status == ChannelStatus.ACTIVE
    assert ch.title == "ЗОЖ без фанатизма"
    assert ch.subscribers == 12000
    assert ch.profile_summary and ch.profile_embedding is not None
    assert "delete" in ch.bot_permissions
    assert "подключён" in max_mock.sent(user_id=OWNER_ID)[-1]["text"]


async def test_insufficient_rights_then_fixed(ctx, max_mock):
    max_mock.permissions = ["write"]
    d = Dispatcher(ctx)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))

    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch.status == ChannelStatus.INSUFFICIENT_RIGHTS
    assert "читать все сообщения" in max_mock.sent(user_id=OWNER_ID)[-1]["text"]

    max_mock.permissions = ["read_all_messages", "write"]
    await d.dispatch(Update.model_validate(load_update("bot_admin_permissions_changed")))
    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch.status == ChannelStatus.ACTIVE


async def test_bot_removed_marks_channel(ctx, max_mock):
    d = Dispatcher(ctx)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))
    await d.dispatch(Update.model_validate(load_update("bot_removed")))
    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert ch.status == ChannelStatus.REMOVED
