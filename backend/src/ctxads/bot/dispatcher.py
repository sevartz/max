"""Транспорт-агностичный диспетчер: и polling, и webhook (через очередь jobs) отдают ему Update."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ctxads.bot.handlers import callbacks, channel, onboarding, posts
from ctxads.context import AppContext
from ctxads.max_api.models import Update

log = logging.getLogger(__name__)

Handler = Callable[[AppContext, Update], Awaitable[None]]


async def _message_created(ctx: AppContext, upd: Update) -> None:
    msg = upd.message
    if msg is None:
        return
    if msg.sender is not None and msg.sender.user_id == ctx.bot_user_id:
        return  # собственные сообщения бота (в т.ч. наша реклама) игнорируем
    if msg.is_channel_post:
        await posts.on_channel_post(ctx, msg)
    elif msg.is_dialog:
        await onboarding.on_private_message(ctx, msg)


HANDLERS: dict[str, Handler] = {
    "bot_started": onboarding.on_bot_started,
    "message_created": _message_created,
    "message_callback": callbacks.on_callback,
    "bot_added": channel.on_bot_added,
    "bot_removed": channel.on_bot_removed,
    "bot_admin_permissions_changed": channel.on_permissions_changed,
}


class Dispatcher:
    def __init__(self, ctx: AppContext, handlers: dict[str, Handler] | None = None) -> None:
        self.ctx = ctx
        self.handlers = handlers or HANDLERS

    async def dispatch(self, update: Update) -> None:
        handler = self.handlers.get(update.update_type)
        if handler is None:
            log.debug("skip update_type=%s", update.update_type)
            return
        await handler(self.ctx, update)
