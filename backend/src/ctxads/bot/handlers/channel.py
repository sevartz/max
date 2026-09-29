from __future__ import annotations

import logging

from ctxads.bot import texts
from ctxads.bot.views import proposal_base_text
from ctxads.context import AppContext
from ctxads.db.models import ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.db.repo import users as users_repo
from ctxads.embeddings.base import centroid
from ctxads.matching import explainer
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Update

log = logging.getLogger(__name__)

REQUIRED_PERMISSIONS = ("read_all_messages", "write")
PROFILE_POSTS = 20


async def on_bot_added(ctx: AppContext, upd: Update) -> None:
    if upd.chat_id is None or upd.user is None or upd.is_channel is False:
        return  # группы в MVP не поддерживаем
    owner = upd.user
    async with ctx.sessions.begin() as s:
        await users_repo.upsert_user(s, owner.user_id, owner.display_name)
        consented = await users_repo.has_consent(s, owner.user_id, ctx.settings.consent_version)
        if not consented:
            await channels_repo.upsert(
                s, upd.chat_id, owner.user_id, status=ChannelStatus.PENDING_CONSENT
            )
    if not consented:
        await notify(
            ctx,
            owner.user_id,
            texts.CONSENT_REQUIRED_FOR_CHANNEL.format(title=texts.untitled(upd.chat_id)),
        )
        return
    await connect(ctx, upd.chat_id, owner.user_id)


async def connect(ctx: AppContext, chat_id: int, owner_user_id: int) -> None:
    """Проверка прав → инфо о канале → профиль → статус active (или инструкция админу)."""
    try:
        me = await ctx.max.get_my_membership(chat_id)
        chat = await ctx.max.get_chat(chat_id)
    except MaxApiError as e:
        log.warning("channel %s: cannot read membership/chat: %s", chat_id, e)
        async with ctx.sessions.begin() as s:
            await channels_repo.upsert(
                s, chat_id, owner_user_id, status=ChannelStatus.INSUFFICIENT_RIGHTS
            )
        await notify(
            ctx,
            owner_user_id,
            texts.CHANNEL_NO_RIGHTS.format(
                title=texts.untitled(chat_id), missing=_missing_label(list(REQUIRED_PERMISSIONS))
            ),
        )
        return

    title = chat.title or texts.untitled(chat_id)
    permissions = list(me.permissions or [])
    missing = [p for p in REQUIRED_PERMISSIONS if p not in permissions]

    async with ctx.sessions.begin() as s:
        existing = await channels_repo.get(s, chat_id)
        has_profile = existing is not None and existing.profile_embedding is not None
        status = ChannelStatus.INSUFFICIENT_RIGHTS if missing else ChannelStatus.ACTIVE
        if existing is not None and existing.status == ChannelStatus.PAUSED and not missing:
            status = ChannelStatus.PAUSED
        ch = await channels_repo.upsert(s, chat_id, owner_user_id, status=status, title=title)
        ch.subscribers = chat.participants_count or 0
        ch.bot_permissions = permissions

    if missing:
        await notify(
            ctx,
            owner_user_id,
            texts.CHANNEL_NO_RIGHTS.format(title=title, missing=_missing_label(missing)),
        )
        return
    if has_profile:
        return  # перепроверка прав: профиль уже есть, LLM не тратим

    summary, embedding = await _build_profile(ctx, chat_id)
    async with ctx.sessions.begin() as s:
        ch = await channels_repo.get(s, chat_id)
        if ch is not None:
            ch.profile_summary = summary
            ch.profile_embedding = embedding
    await notify(
        ctx,
        owner_user_id,
        texts.CHANNEL_CONNECTED.format(
            title=title,
            subscribers=chat.participants_count or 0,
            profile=summary or texts.PROFILE_UNKNOWN,
        ),
    )


async def _build_profile(ctx: AppContext, chat_id: int) -> tuple[str | None, list[float] | None]:
    try:
        messages = await ctx.max.get_messages(chat_id=chat_id, count=PROFILE_POSTS)
    except MaxApiError as e:
        log.warning("channel %s: cannot read posts: %s", chat_id, e)
        return None, None
    posts = [m.text for m in messages if m.text.strip()]
    if not posts:
        return None, None
    summary = await explainer.channel_profile(ctx.llm, posts)
    embedding = centroid(await ctx.embedder.embed_passages(posts))
    return summary, embedding


async def on_bot_removed(ctx: AppContext, upd: Update) -> None:
    if upd.chat_id is None:
        return
    async with ctx.sessions.begin() as s:
        ch = await channels_repo.get(s, upd.chat_id)
        if ch is None:
            return
        ch.status = ChannelStatus.REMOVED
        cancelled = await proposals_repo.cancel_open_for_channel(s, upd.chat_id, ctx.now())
        to_edit = [
            (p.admin_message_mid, await proposal_base_text(s, p))
            for p in cancelled
            if p.admin_message_mid
        ]
        owner, title = ch.owner_user_id, ch.title or texts.untitled(ch.chat_id)
    for mid, base in to_edit:
        await _safe_edit(ctx, mid, texts.cancelled(base))
    await notify(ctx, owner, texts.CHANNEL_REMOVED.format(title=title))


async def on_permissions_changed(ctx: AppContext, upd: Update) -> None:
    if upd.chat_id is None:
        return
    async with ctx.sessions.begin() as s:
        ch = await channels_repo.get(s, upd.chat_id)
        if ch is None or ch.status in (ChannelStatus.REMOVED, ChannelStatus.PENDING_CONSENT):
            return
        owner = ch.owner_user_id
    await connect(ctx, upd.chat_id, owner)


async def notify(ctx: AppContext, user_id: int, text: str) -> None:
    """Сообщение админу в личку; если он не стартовал бота — просто логируем."""
    try:
        await ctx.max.send_message(user_id=user_id, text=text)
    except MaxApiError as e:
        log.info("cannot notify user %s: %s", user_id, e)


async def _safe_edit(ctx: AppContext, mid: str, text: str) -> None:
    try:
        await ctx.max.edit_message(mid, text=text, attachments=[])
    except MaxApiError as e:
        log.info("cannot edit %s: %s", mid, e)


def _missing_label(missing: list[str]) -> str:
    return ", ".join(texts.PERMISSION_LABELS.get(p, p) for p in missing)
