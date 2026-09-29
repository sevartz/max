from __future__ import annotations

import ipaddress
import logging
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from ctxads.ads.tracking import utm_params, with_params
from ctxads.bot import keyboards, texts
from ctxads.bot.handlers.channel import REQUIRED_PERMISSIONS, notify
from ctxads.bot.handlers.settings import can_delete
from ctxads.context import AppContext
from ctxads.db.models import Ad, Advertiser, ChannelStatus, Placement, ProposalStatus
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.jobs import queue
from ctxads.max_api.errors import MaxApiError

log = logging.getLogger(__name__)


def marking(advertiser: Advertiser, ad: Ad) -> str:
    # Рекламодатели из кабинета бота ИНН не указывают — тогда без него.
    template = texts.AD_MARKING if advertiser.inn else texts.AD_MARKING_NO_INN
    return template.format(legal_name=advertiser.legal_name, inn=advertiser.inn, erid=ad.erid)


def build_ad_post(
    ad: Ad, advertiser: Advertiser, tracking_url: str
) -> tuple[str, list[dict[str, Any]]]:
    """Креатив + обязательная маркировка (закон о рекламе) + кнопка-ссылка на трекер."""
    text = f"{ad.title}\n\n{ad.body}\n\n{marking(advertiser, ad)}"
    attachments: list[dict[str, Any]] = []
    if ad.image_url:
        # TODO(verify-api): загрузка картинки по url в AttachmentRequest(image).
        attachments.append({"type": "image", "payload": {"url": ad.image_url}})
    attachments.append(keyboards.ad_link(tracking_url))
    return text, attachments


def tracking_url(ctx: AppContext, token: str) -> str:
    return f"{ctx.settings.public_base_url.rstrip('/')}/r/{token}"


def is_public_base_url(url: str) -> bool:
    """False для localhost и частных адресов — ссылки на них MAX в кнопках не принимает."""
    host = (urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


def button_url(ctx: AppContext, ad: Ad, placement: Placement) -> str:
    if is_public_base_url(ctx.settings.public_base_url):
        return tracking_url(ctx, placement.token)
    # Без публичного адреса трекер недоступен снаружи: ведём сразу к рекламодателю,
    # клики при этом не считаются (локальная разработка).
    return with_params(ad.url, utm_params(ad.key, placement.chat_id))


async def publish(ctx: AppContext, proposal_id: int) -> None:
    """Job `publish`: перепроверяем права, бюджет и частоту — могли измениться за задержку."""
    now = ctx.now()
    async with ctx.sessions.begin() as s:
        p = await proposals_repo.get(s, proposal_id, for_update=True)
        if p is None or p.status != ProposalStatus.APPROVED:
            return
        channel = await channels_repo.get(s, p.chat_id)
        ad = await ads_repo.get(s, p.ad_id)
        advertiser = await ads_repo.get_advertiser(s, ad.advertiser_id) if ad else None
        cs = await channels_repo.get_settings(s, p.chat_id)
        assert channel is not None and ad is not None and advertiser is not None
        owner = channel.owner_user_id

        fail_reason = None
        if channel.status != ChannelStatus.ACTIVE:
            fail_reason = texts.PUBLISH_FAIL_CHANNEL
        elif ad.status != "active" or ad.budget_left <= 0:
            fail_reason = texts.PUBLISH_FAIL_BUDGET
        elif (
            await proposals_repo.placements_since(s, p.chat_id, now - timedelta(hours=24))
            >= cs.max_ads_per_day
        ):
            fail_reason = texts.PUBLISH_FAIL_FREQUENCY
        else:
            permissions = await _current_permissions(ctx, p.chat_id)
            if permissions is None or any(r not in permissions for r in REQUIRED_PERMISSIONS):
                fail_reason = texts.PUBLISH_FAIL_RIGHTS
            else:
                channel.bot_permissions = permissions

        if fail_reason:
            p.status = ProposalStatus.CANCELLED
        else:
            placement = await placements_repo.get_by_proposal(s, p.id)
            if placement is None:
                placement = await placements_repo.add(
                    s,
                    Placement(
                        proposal_id=p.id,
                        ad_id=ad.id,
                        chat_id=p.chat_id,
                        token=secrets.token_urlsafe(9),
                    ),
                )
            text, attachments = build_ad_post(ad, advertiser, button_url(ctx, ad, placement))
            placement_id = placement.id
            deletable = can_delete(channel.bot_permissions)
            ttl_hours = cs.ad_ttl_hours
            ad_title, channel_title = ad.title, channel.title or texts.untitled(channel.chat_id)

    if fail_reason:
        log.info("proposal %s not published: %s", proposal_id, fail_reason)
        await notify(ctx, owner, texts.publish_failed(ad.title, fail_reason))
        return

    try:
        sent = await ctx.max.send_message(chat_id=p.chat_id, text=text, attachments=attachments)
    except MaxApiError as e:
        if e.retryable:
            raise  # воркер повторит задачу
        log.warning("proposal %s: publish failed: %s", proposal_id, e)
        async with ctx.sessions.begin() as s:
            p2 = await proposals_repo.get(s, proposal_id, for_update=True)
            if p2 is not None:
                p2.status = ProposalStatus.CANCELLED
        await notify(ctx, owner, texts.publish_failed(ad_title, str(e.message or e.status)))
        return

    published_at = ctx.now()
    async with ctx.sessions.begin() as s:
        placement = await placements_repo.get(s, placement_id)
        p2 = await proposals_repo.get(s, proposal_id, for_update=True)
        assert placement is not None and p2 is not None
        placement.mid = sent.mid if sent else None
        placement.published_at = published_at
        p2.status = ProposalStatus.PUBLISHED
        if deletable and ttl_hours > 0 and placement.mid:
            await queue.enqueue(
                s,
                "delete_ad",
                {"placement_id": placement.id},
                run_at=published_at + timedelta(hours=ttl_hours),
            )
    await notify(ctx, owner, texts.published(ad_title, channel_title))


async def _current_permissions(ctx: AppContext, chat_id: int) -> list[str] | None:
    try:
        me = await ctx.max.get_my_membership(chat_id)
    except MaxApiError as e:
        log.warning("chat %s: cannot check rights: %s", chat_id, e)
        return None
    return list(me.permissions or [])


async def delete_ad(ctx: AppContext, placement_id: int) -> None:
    """Job `delete_ad`: автоудаление рекламы через ad_ttl_hours (нужно право delete)."""
    async with ctx.sessions() as s:
        placement = await placements_repo.get(s, placement_id)
        if placement is None or placement.deleted_at is not None or not placement.mid:
            return
        mid = placement.mid
    try:
        await ctx.max.delete_message(mid)
    except MaxApiError as e:
        if e.retryable:
            raise
        log.warning("placement %s: cannot delete: %s", placement_id, e)
        return
    async with ctx.sessions.begin() as s:
        placement = await placements_repo.get(s, placement_id)
        if placement is not None:
            placement.deleted_at = ctx.now()
