"""Shared channel setting changes used by both the bot and the mini-app."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Channel, ChannelStatus
from ctxads.db.repo import channels as channels_repo
from ctxads.matching.categories import CATEGORIES, REGULATED


async def set_category_allowed(s: AsyncSession, chat_id: int, category: str, allowed: bool) -> bool:
    """Set a category to its requested state; safe to retry from the mini-app."""
    if category not in CATEGORIES or category == "other":
        return False
    settings = await channels_repo.get_settings(s, chat_id, for_update=True)
    if category in REGULATED:
        allowed_values = set(settings.allow_regulated or [])
        blocked_values = set(settings.blocked_categories or [])
        if allowed:
            allowed_values.add(category)
            blocked_values.discard(category)
        else:
            allowed_values.discard(category)
            blocked_values.add(category)
        settings.allow_regulated = sorted(allowed_values)
        settings.blocked_categories = sorted(blocked_values)
    else:
        values = set(settings.blocked_categories or [])
        if allowed:
            values.discard(category)
        else:
            values.add(category)
        settings.blocked_categories = sorted(values)
    return True


async def toggle_category(s: AsyncSession, chat_id: int, category: str) -> bool:
    """Toggle the bot's existing category button while serializing concurrent changes."""
    if category not in CATEGORIES or category == "other":
        return False
    settings = await channels_repo.get_settings(s, chat_id, for_update=True)
    if category in REGULATED:
        currently_allowed = category in (settings.allow_regulated or []) and category not in (
            settings.blocked_categories or []
        )
        allowed = not currently_allowed
    else:
        allowed = category in (settings.blocked_categories or [])
    return await set_category_allowed(s, chat_id, category, allowed)


def set_paused(channel: Channel, paused: bool) -> bool:
    if channel.status not in (ChannelStatus.ACTIVE, ChannelStatus.PAUSED):
        return False
    channel.status = ChannelStatus.PAUSED if paused else ChannelStatus.ACTIVE
    return True


async def toggle_paused(s: AsyncSession, chat_id: int) -> bool:
    channel = await channels_repo.get(s, chat_id, for_update=True)
    if channel is None or channel.status not in (ChannelStatus.ACTIVE, ChannelStatus.PAUSED):
        return False
    return set_paused(channel, channel.status == ChannelStatus.ACTIVE)
