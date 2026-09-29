from __future__ import annotations

from ctxads.bot.dispatcher import Dispatcher
from ctxads.db.models import ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.max_api.models import Update
from tests.conftest import CHANNEL_ID, OWNER_ID, callback, load_update


def _command(text: str) -> Update:
    raw = load_update("message_created_dialog")
    raw["message"]["body"]["text"] = text
    return Update.model_validate(raw)


async def test_settings_toggle_categories_and_pause(seeded, max_mock):
    d = Dispatcher(seeded)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))

    await d.dispatch(_command("/settings"))
    msg = max_mock.sent(user_id=OWNER_ID)[-1]
    assert "ЗОЖ без фанатизма" in msg["text"] and "удаляю рекламу через 48 ч" in msg["text"]
    payloads = [b["payload"] for row in msg["attachments"][0]["payload"]["buttons"] for b in row]
    assert f"s:{CHANNEL_ID}:b:food" in payloads
    assert f"s:{CHANNEL_ID}:r:medicine" in payloads

    await d.dispatch(callback(f"s:{CHANNEL_ID}:b:food"))
    await d.dispatch(callback(f"s:{CHANNEL_ID}:r:medicine"))
    await d.dispatch(callback(f"s:{CHANNEL_ID}:r:food"))  # food не регулируемая — игнор
    async with seeded.sessions() as s:
        cs = await channels_repo.get_settings(s, CHANNEL_ID)
    assert cs.blocked_categories == ["food"]
    assert cs.allow_regulated == ["medicine"]
    assert "Заблокированные категории: Еда" in max_mock.edits()[-1]["text"]

    await d.dispatch(callback(f"s:{CHANNEL_ID}:b:food"))
    await d.dispatch(callback(f"s:{CHANNEL_ID}:pause"))
    async with seeded.sessions() as s:
        cs = await channels_repo.get_settings(s, CHANNEL_ID)
        ch = await channels_repo.get(s, CHANNEL_ID)
    assert cs.blocked_categories == []
    assert ch.status == ChannelStatus.PAUSED


async def test_settings_of_foreign_channel_denied(seeded, max_mock):
    d = Dispatcher(seeded)
    await d.dispatch(callback("c:accept"))
    await d.dispatch(Update.model_validate(load_update("bot_added")))
    await d.dispatch(callback(f"s:{CHANNEL_ID}:b:food", user_id=555))
    async with seeded.sessions() as s:
        cs = await channels_repo.get_settings(s, CHANNEL_ID)
    assert cs.blocked_categories == []


async def test_help_and_unknown_command(seeded, max_mock):
    d = Dispatcher(seeded)
    await d.dispatch(_command("/help"))
    await d.dispatch(_command("привет"))
    texts_ = [m["text"] for m in max_mock.sent(user_id=OWNER_ID)]
    assert "/settings" in texts_[0] and "/help" in texts_[1]
