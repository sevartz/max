from __future__ import annotations

import logging

from ctxads.bot import keyboards, texts
from ctxads.bot.handlers import cabinet, channel, settings, stats
from ctxads.context import AppContext
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import users as users_repo
from ctxads.max_api.models import Callback, Message, Update, User

log = logging.getLogger(__name__)


async def on_bot_started(ctx: AppContext, upd: Update) -> None:
    if upd.user is not None:
        await start(ctx, upd.user)


async def on_private_message(ctx: AppContext, msg: Message) -> None:
    user = msg.sender
    if user is None:
        return
    text = msg.text.strip()
    command = text.split(maxsplit=1)[0].lower() if text else ""
    if command == "/cancel":
        await cabinet.cancel(ctx, user.user_id)
        return
    if command.startswith("/"):
        await cabinet.reset(ctx, user.user_id)  # любая команда прерывает пошаговый ввод
    elif await cabinet.on_text(ctx, user, text):
        return
    if command == "/start":
        await start(ctx, user)
    elif command == "/help":
        app_button = keyboards.miniapp(ctx.settings.miniapp_url)
        await ctx.max.send_message(
            user_id=user.user_id,
            text=texts.HELP,
            attachments=[app_button] if app_button else None,
        )
    elif command == "/settings":
        await settings.show(ctx, user.user_id)
    elif command == "/stats":
        await stats.show(ctx, user.user_id)
    elif command == "/cabinet":
        await cabinet.show(ctx, user)
    else:
        await ctx.max.send_message(user_id=user.user_id, text=texts.UNKNOWN_COMMAND)


async def start(ctx: AppContext, user: User) -> None:
    async with ctx.sessions.begin() as s:
        await users_repo.upsert_user(s, user.user_id, user.display_name)
        consented = await users_repo.has_consent(s, user.user_id, ctx.settings.consent_version)
    if consented:
        app_button = keyboards.miniapp(ctx.settings.miniapp_url)
        await ctx.max.send_message(
            user_id=user.user_id,
            text=texts.CONSENT_ALREADY,
            attachments=[app_button] if app_button else None,
        )
    else:
        await ctx.max.send_message(
            user_id=user.user_id,
            text=texts.CONSENT,
            attachments=[keyboards.with_miniapp(keyboards.consent(), ctx.settings.miniapp_url)],
        )


async def on_consent_callback(ctx: AppContext, cb: Callback, msg: Message | None) -> None:
    await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_OK)
    pending = await accept_consent(ctx, cb.user.user_id, cb.user.display_name)
    if msg is not None and msg.mid:
        await ctx.max.edit_message(msg.mid, text=texts.CONSENT, attachments=[])
    app_button = keyboards.miniapp(ctx.settings.miniapp_url)
    await ctx.max.send_message(
        user_id=cb.user.user_id,
        text=texts.CONSENT_ACCEPTED,
        attachments=[app_button] if app_button else None,
    )
    for chat_id in pending:
        await channel.connect(ctx, chat_id, cb.user.user_id)


async def accept_consent(ctx: AppContext, user_id: int, display_name: str | None) -> list[int]:
    """Persist the existing consent and return channels that were waiting for it."""
    async with ctx.sessions.begin() as s:
        await users_repo.upsert_user(s, user_id, display_name)
        await users_repo.add_consent(s, user_id, ctx.settings.consent_version)
        return [c.chat_id for c in await channels_repo.list_pending_consent(s, user_id)]
