from __future__ import annotations

import logging

from ctxads.ads import channel_settings as channel_settings_service
from ctxads.bot import keyboards, texts
from ctxads.context import AppContext
from ctxads.db.models import ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.matching.categories import CATEGORIES, REGULATED
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Callback, Message

log = logging.getLogger(__name__)

DELETE_PERMISSIONS = frozenset({"delete", "delete_message", "post_edit_delete_message"})


def can_delete(permissions: list[str] | None) -> bool:
    return bool(DELETE_PERMISSIONS.intersection(permissions or []))


async def show(ctx: AppContext, user_id: int) -> None:
    async with ctx.sessions() as s:
        channels = await channels_repo.list_by_owner(s, user_id)
    if not channels:
        app_button = keyboards.miniapp(ctx.settings.miniapp_url)
        await ctx.max.send_message(
            user_id=user_id,
            text=texts.SETTINGS_NO_CHANNELS,
            attachments=[app_button] if app_button else None,
        )
        return
    if len(channels) == 1:
        text, kb = await _channel_view(ctx, channels[0].chat_id)
    else:
        text, kb = await _list_view(ctx, user_id)
    await ctx.max.send_message(
        user_id=user_id,
        text=text,
        attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)],
    )


async def _list_view(ctx: AppContext, user_id: int) -> tuple[str, keyboards.Keyboard]:
    async with ctx.sessions() as s:
        channels = await channels_repo.list_by_owner(s, user_id)
    items = [
        (
            c.chat_id,
            f"{c.title or texts.untitled(c.chat_id)} — "
            f"{texts.STATUS_LABELS.get(c.status, c.status)}",
        )
        for c in channels
    ]
    return texts.SETTINGS_PICK, keyboards.channels_list(items)


async def _channel_view(ctx: AppContext, chat_id: int) -> tuple[str, keyboards.Keyboard]:
    async with ctx.sessions() as s:
        ch = await channels_repo.get(s, chat_id)
        cs = await channels_repo.get_settings(s, chat_id)
        await s.commit()
    assert ch is not None
    text = texts.settings_channel(
        ch.title or texts.untitled(chat_id),
        ch.status,
        list(cs.blocked_categories or []),
        list(cs.allow_regulated or []),
        can_delete(ch.bot_permissions),
        cs.ad_ttl_hours,
    )
    kb = keyboards.channel_settings(
        chat_id,
        list(cs.blocked_categories or []),
        list(cs.allow_regulated or []),
        paused=ch.status == ChannelStatus.PAUSED,
    )
    return text, kb


async def on_settings_callback(ctx: AppContext, cb: Callback, msg: Message | None) -> None:
    parts = (cb.payload or "").split(":")
    try:
        chat_id = int(parts[1])
        action = parts[2]
    except (IndexError, ValueError):
        await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_STALE)
        return

    if action == "list":
        await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_OK)
        text, kb = await _list_view(ctx, cb.user.user_id)
        await _edit(ctx, msg, text, kb)
        return

    async with ctx.sessions.begin() as s:
        ch = await channels_repo.get(s, chat_id)
        if ch is None or ch.owner_user_id != cb.user.user_id:
            ok = False
        else:
            ok = True
            category = parts[3] if len(parts) > 3 else ""
            # «b» — только обычные категории, «r» — только регулируемые (как на кнопках).
            if (action == "b" and category in CATEGORIES and category not in REGULATED) or (
                action == "r" and category in REGULATED
            ):
                ok = await channel_settings_service.toggle_category(s, chat_id, category)
            elif action == "pause":
                ok = await channel_settings_service.toggle_paused(s, chat_id)
    await ctx.max.answer_callback(
        cb.callback_id, notification=texts.ACK_OK if ok else texts.ACK_NOT_OWNER
    )
    if ok:
        text, kb = await _channel_view(ctx, chat_id)
        await _edit(ctx, msg, text, kb)


async def _edit(ctx: AppContext, msg: Message | None, text: str, kb: keyboards.Keyboard) -> None:
    if msg is None or not msg.mid:
        return
    try:
        await ctx.max.edit_message(
            msg.mid, text=text, attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)]
        )
    except MaxApiError as e:
        log.warning("cannot edit settings message: %s", e)
