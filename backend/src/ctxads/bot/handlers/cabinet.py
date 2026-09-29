"""Кабинет рекламодателя в личке: /cabinet, пошаговый ввод и кнопки "a:…".

Шаг диалога хранится в dialog_states: так ввод переживает рестарт и работает при нескольких
воркерах. Кнопки несут только id — остальное берём из БД.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.ads import cabinet
from ctxads.bot import keyboards, texts
from ctxads.context import AppContext
from ctxads.db.models import Ad
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import dialogs as dialogs_repo
from ctxads.db.repo import placements as placements_repo
from ctxads.max_api.errors import MaxApiError
from ctxads.max_api.models import Callback, Message, User

log = logging.getLogger(__name__)

FLOW = "cabinet"
EMPTY_STATS: dict[str, object] = {"placements": 0, "views": 0, "clicks": 0, "spent": Decimal(0)}

FIELD_BAD_INPUT = {
    "title": texts.CAB_BAD_TITLE,
    "body": texts.CAB_BAD_BODY,
    "url": texts.CAB_BAD_URL,
    "price": texts.CAB_BAD_MONEY,
}


def _parse_field(field: str, text: str) -> str | Decimal | None:
    if field == "title":
        return cabinet.parse_text(text, cabinet.TITLE_MAX)
    if field == "body":
        return cabinet.parse_text(text, cabinet.BODY_MAX)
    if field == "url":
        return cabinet.parse_url(text)
    return cabinet.parse_money(text)


def _status(ad: Ad) -> str:
    if ad.status == cabinet.ACTIVE and ad.budget_left <= 0:
        return "exhausted"
    return ad.status


# --- вход, отмена ---


async def show(ctx: AppContext, user: User) -> None:
    async with ctx.sessions.begin() as s:
        await dialogs_repo.clear(s, user.user_id)
        advertiser = await ads_repo.get_advertiser_by_owner(s, user.user_id)
        if advertiser is None:
            await dialogs_repo.set_state(s, user.user_id, FLOW, "company", {})
    if advertiser is None:
        await ctx.max.send_message(user_id=user.user_id, text=texts.CAB_ASK_COMPANY)
        return
    await _send_menu(ctx, user.user_id)


async def reset(ctx: AppContext, user_id: int) -> bool:
    """Сбросить незавершённый ввод. True — было что сбрасывать."""
    async with ctx.sessions.begin() as s:
        state = await dialogs_repo.get(s, user_id)
        if state is not None:
            await dialogs_repo.clear(s, user_id)
    return state is not None


async def cancel(ctx: AppContext, user_id: int) -> None:
    had_state = await reset(ctx, user_id)
    text = texts.CAB_CANCELLED if had_state else texts.CAB_NOTHING_TO_CANCEL
    await ctx.max.send_message(user_id=user_id, text=text)


# --- текстовый ввод ---

TextStep = Callable[[AppContext, int, str, dict[str, Any]], Awaitable[None]]


async def on_text(ctx: AppContext, user: User, text: str) -> bool:
    """Обработать сообщение как ответ на шаг кабинета. False — кабинет его не ждал."""
    async with ctx.sessions() as s:
        state = await dialogs_repo.get(s, user.user_id)
    if state is None or state.flow != FLOW:
        return False
    step = TEXT_STEPS.get(state.step)
    if step is None:  # этот шаг ждёт нажатия кнопки
        await ctx.max.send_message(user_id=user.user_id, text=texts.CAB_USE_BUTTONS)
    else:
        await step(ctx, user.user_id, text, dict(state.data))
    return True


async def _ask(
    ctx: AppContext,
    user_id: int,
    step: str,
    data: dict[str, Any],
    prompt: str,
    keyboard: keyboards.Keyboard | None = None,
) -> None:
    async with ctx.sessions.begin() as s:
        await dialogs_repo.set_state(s, user_id, FLOW, step, data)
    await ctx.max.send_message(
        user_id=user_id,
        text=prompt,
        attachments=[keyboards.with_miniapp(keyboard, ctx.settings.miniapp_url)]
        if keyboard
        else ([keyboards.miniapp(ctx.settings.miniapp_url)] if ctx.settings.miniapp_url else None),
    )


async def _on_company(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    name = cabinet.parse_text(text, cabinet.NAME_MAX)
    if name is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_COMPANY)
        return
    async with ctx.sessions.begin() as s:
        if await ads_repo.get_advertiser_by_owner(s, user_id) is None:
            await cabinet.create_advertiser(s, user_id, name)
        await dialogs_repo.clear(s, user_id)
    await _send_menu(ctx, user_id)


async def _on_title(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    title = cabinet.parse_text(text, cabinet.TITLE_MAX)
    if title is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_TITLE)
        return
    await _ask(ctx, user_id, "body", {**data, "title": title}, texts.CAB_ASK_BODY)


async def _on_body(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    body = cabinet.parse_text(text, cabinet.BODY_MAX)
    if body is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_BODY)
        return
    await _ask(ctx, user_id, "url", {**data, "body": body}, texts.CAB_ASK_URL)


async def _on_url(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    url = cabinet.parse_url(text)
    if url is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_URL)
        return
    await _ask(
        ctx,
        user_id,
        "category",
        {**data, "url": url},
        texts.CAB_ASK_CATEGORY,
        keyboards.cabinet_categories(cabinet.CATEGORY_CHOICES),
    )


async def _on_price(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    price = cabinet.parse_money(text)
    if price is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_MONEY)
        return
    await _ask(ctx, user_id, "budget", {**data, "price": str(price)}, texts.CAB_ASK_BUDGET)


async def _on_budget(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    budget = cabinet.parse_money(text)
    price = Decimal(data["price"])
    if budget is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_MONEY)
        return
    if budget < price:
        await ctx.max.send_message(
            user_id=user_id, text=texts.CAB_BAD_BUDGET.format(price=texts.rub(price))
        )
        return
    draft = {**data, "budget": str(budget)}
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
    company = advertiser.legal_name if advertiser else ""
    await _ask(
        ctx,
        user_id,
        "confirm",
        draft,
        texts.cab_preview(draft, company),
        keyboards.cabinet_confirm(),
    )


async def _on_edit(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    field = data["field"]
    value = _parse_field(field, text)
    if value is None:
        await ctx.max.send_message(user_id=user_id, text=FIELD_BAD_INPUT[field])
        return
    async with ctx.sessions.begin() as s:
        ad = await _owned_ad(s, user_id, int(data["ad_id"]))
        if ad is not None:
            await cabinet.update_field(ctx.embedder, ad, field, value)
        await dialogs_repo.clear(s, user_id)
    await _after_ad_change(ctx, user_id, ad, texts.CAB_SAVED)


async def _on_topup(ctx: AppContext, user_id: int, text: str, data: dict[str, Any]) -> None:
    amount = cabinet.parse_money(text)
    if amount is None:
        await ctx.max.send_message(user_id=user_id, text=texts.CAB_BAD_MONEY)
        return
    async with ctx.sessions.begin() as s:
        ad = await _owned_ad(s, user_id, int(data["ad_id"]), for_update=True)
        if ad is not None:
            cabinet.top_up(ad, amount)
        await dialogs_repo.clear(s, user_id)
    note = texts.CAB_TOPPED_UP.format(title=ad.title, amount=texts.rub(amount)) if ad else ""
    await _after_ad_change(ctx, user_id, ad, note)


TEXT_STEPS: dict[str, TextStep] = {
    "company": _on_company,
    "title": _on_title,
    "body": _on_body,
    "url": _on_url,
    "price": _on_price,
    "budget": _on_budget,
    "edit": _on_edit,
    "topup": _on_topup,
}


# --- кнопки ---


async def on_callback(ctx: AppContext, cb: Callback, msg: Message | None) -> None:
    parts = (cb.payload or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    user_id = cb.user.user_id
    handler = CALLBACKS.get(action)
    if handler is None:
        await ctx.max.answer_callback(cb.callback_id, notification=texts.ACK_CAB_STALE)
        return
    ok = await handler(ctx, user_id, arg, parts[3] if len(parts) > 3 else "", msg)
    await ctx.max.answer_callback(
        cb.callback_id, notification=texts.ACK_OK if ok else texts.ACK_CAB_STALE
    )


CallbackAction = Callable[[AppContext, int, str, str, Message | None], Awaitable[bool]]


async def _cb_menu(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    view = await _menu_view(ctx, user_id)
    if view is None:
        return False
    await _edit_or_send(ctx, user_id, msg, *view)
    return True


async def _cb_new(ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None) -> bool:
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
    if advertiser is None:
        return False
    await _ask(ctx, user_id, "title", {}, texts.CAB_ASK_TITLE)
    return True


async def _cb_category(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    if arg not in cabinet.CATEGORY_CHOICES:
        return False
    async with ctx.sessions() as s:
        state = await dialogs_repo.get(s, user_id)
    if state is None or state.flow != FLOW:
        return False
    data = dict(state.data)
    if state.step == "category":
        await _ask(
            ctx,
            user_id,
            "model",
            {**data, "category": arg},
            texts.CAB_ASK_MODEL,
            keyboards.cabinet_pricing_models(cabinet.PRICING_MODELS),
        )
        return True
    if state.step == "edit_category":
        async with ctx.sessions.begin() as s:
            ad = await _owned_ad(s, user_id, int(data["ad_id"]))
            if ad is not None:
                await cabinet.update_field(ctx.embedder, ad, "category", arg)
            await dialogs_repo.clear(s, user_id)
        await _after_ad_change(ctx, user_id, ad, texts.CAB_SAVED)
        return ad is not None
    return False


async def _cb_pricing_model(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    if arg not in cabinet.PRICING_MODELS:
        return False
    async with ctx.sessions() as s:
        state = await dialogs_repo.get(s, user_id)
    if state is None or state.flow != FLOW or state.step != "model":
        return False
    data = {**state.data, "pricing_model": arg}
    await _ask(ctx, user_id, "price", data, texts.cab_ask_price(arg))
    return True


async def _cb_launch(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    async with ctx.sessions.begin() as s:
        # FOR UPDATE: двойное нажатие «Запустить» не создаст два объявления.
        state = await dialogs_repo.get(s, user_id, for_update=True)
        if state is None or state.flow != FLOW or state.step != "confirm":
            return False
        advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
        if advertiser is None:
            return False
        ad = await cabinet.create_ad(s, ctx.embedder, advertiser, dict(state.data))
        await dialogs_repo.clear(s, user_id)
        ad_id, title = ad.id, ad.title
    await _drop_buttons(ctx, msg)
    await ctx.max.send_message(user_id=user_id, text=texts.cab_launched(title))
    await _send_card(ctx, user_id, ad_id)
    return True


async def _cb_cancel(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    await _drop_buttons(ctx, msg)
    await cancel(ctx, user_id)
    return True


async def _cb_stats(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
        if advertiser is None:
            return False
        ads = await ads_repo.list_by_advertiser(s, advertiser.id)
        stats = await placements_repo.ad_stats(s, [ad.id for ad in ads])
    totals: dict[str, Any] = {k: 0 for k in ("placements", "views", "clicks")} | {
        "spent": Decimal(0)
    }
    lines = []
    for ad in ads:
        st = stats.get(ad.id, EMPTY_STATS)
        for key in totals:
            totals[key] += st[key]
        lines.append(texts.cab_stats_line(ad.title, st, ad.budget_left))
    await ctx.max.send_message(
        user_id=user_id, text=texts.cab_stats(advertiser.name, lines, totals)
    )
    return True


async def _cb_ad(ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None) -> bool:
    ad_id = _int(arg)
    async with ctx.sessions() as s:
        ad = await _owned_ad(s, user_id, ad_id) if ad_id else None
    if ad is None:
        return False
    await _edit_or_send(ctx, user_id, msg, *await _card_view(ctx, ad.id))
    return True


async def _cb_pause(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    ad_id = _int(arg)
    async with ctx.sessions.begin() as s:
        ad = await _owned_ad(s, user_id, ad_id, for_update=True) if ad_id else None
        if ad is not None:
            cabinet.toggle_pause(ad)
    if ad is None:
        return False
    await _edit_or_send(ctx, user_id, msg, *await _card_view(ctx, ad.id))
    return True


async def _cb_topup(
    ctx: AppContext, user_id: int, arg: str, extra: str, msg: Message | None
) -> bool:
    ad_id = _int(arg)
    async with ctx.sessions() as s:
        ad = await _owned_ad(s, user_id, ad_id) if ad_id else None
    if ad is None:
        return False
    prompt = texts.CAB_ASK_TOPUP.format(title=ad.title)
    await _ask(ctx, user_id, "topup", {"ad_id": ad.id}, prompt)
    return True


async def _cb_edit(
    ctx: AppContext, user_id: int, arg: str, field: str, msg: Message | None
) -> bool:
    ad_id = _int(arg)
    async with ctx.sessions() as s:
        ad = await _owned_ad(s, user_id, ad_id) if ad_id else None
    if ad is None:
        return False
    if field == "category":
        await _ask(
            ctx,
            user_id,
            "edit_category",
            {"ad_id": ad.id},
            texts.CAB_ASK_NEW_CATEGORY,
            keyboards.cabinet_categories(cabinet.CATEGORY_CHOICES),
        )
        return True
    if field not in cabinet.EDITABLE_TEXT_FIELDS:
        return False
    data = {"ad_id": ad.id, "field": field}
    await _ask(ctx, user_id, "edit", data, texts.CAB_EDIT_PROMPTS[field])
    return True


CALLBACKS: dict[str, CallbackAction] = {
    "menu": _cb_menu,
    "new": _cb_new,
    "cat": _cb_category,
    "pm": _cb_pricing_model,
    "go": _cb_launch,
    "cancel": _cb_cancel,
    "stats": _cb_stats,
    "ad": _cb_ad,
    "pause": _cb_pause,
    "top": _cb_topup,
    "ed": _cb_edit,
}


# --- представления ---


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


async def _owned_ad(
    s: AsyncSession, user_id: int, ad_id: int, *, for_update: bool = False
) -> Ad | None:
    """Объявление, только если оно принадлежит рекламодателю этого пользователя."""
    advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
    if advertiser is None:
        return None
    ad = await (ads_repo.get_for_update(s, ad_id) if for_update else ads_repo.get(s, ad_id))
    return ad if ad is not None and ad.advertiser_id == advertiser.id else None


async def _menu_view(ctx: AppContext, user_id: int) -> tuple[str, keyboards.Keyboard] | None:
    async with ctx.sessions() as s:
        advertiser = await ads_repo.get_advertiser_by_owner(s, user_id)
        if advertiser is None:
            return None
        ads = await ads_repo.list_by_advertiser(s, advertiser.id)
    text = texts.cab_menu(
        advertiser.name,
        len(ads),
        sum(1 for ad in ads if _status(ad) == cabinet.ACTIVE),
        sum((ad.budget_left for ad in ads), Decimal(0)),
    )
    kb = keyboards.cabinet_menu([(ad.id, texts.cab_ad_button(_status(ad), ad.title)) for ad in ads])
    return text, kb


async def _card_view(ctx: AppContext, ad_id: int) -> tuple[str, keyboards.Keyboard]:
    async with ctx.sessions() as s:
        ad = await ads_repo.get(s, ad_id)
        assert ad is not None
        st = (await placements_repo.ad_stats(s, [ad_id])).get(ad_id, EMPTY_STATS)
    text = texts.cab_ad_card(
        title=ad.title,
        body=ad.body,
        url=ad.url,
        category=ad.category,
        pricing_model=ad.pricing_model,
        price=ad.price,
        status=_status(ad),
        budget_left=ad.budget_left,
        budget_total=ad.budget_total,
        st=st,
    )
    return text, keyboards.cabinet_ad(ad_id, paused=ad.status == cabinet.PAUSED)


async def _send_menu(ctx: AppContext, user_id: int) -> None:
    view = await _menu_view(ctx, user_id)
    if view is not None:
        text, kb = view
        await ctx.max.send_message(
            user_id=user_id,
            text=text,
            attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)],
        )


async def _send_card(ctx: AppContext, user_id: int, ad_id: int) -> None:
    text, kb = await _card_view(ctx, ad_id)
    await ctx.max.send_message(
        user_id=user_id,
        text=text,
        attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)],
    )


async def _after_ad_change(ctx: AppContext, user_id: int, ad: Ad | None, note: str) -> None:
    if ad is None:
        await ctx.max.send_message(user_id=user_id, text=texts.ACK_CAB_STALE)
        return
    if note:
        await ctx.max.send_message(user_id=user_id, text=note)
    await _send_card(ctx, user_id, ad.id)


async def _edit_or_send(
    ctx: AppContext, user_id: int, msg: Message | None, text: str, kb: keyboards.Keyboard
) -> None:
    if msg is not None and msg.mid:
        try:
            await ctx.max.edit_message(
                msg.mid,
                text=text,
                attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)],
            )
            return
        except MaxApiError as e:
            log.info("cannot edit cabinet message %s: %s", msg.mid, e)
    await ctx.max.send_message(
        user_id=user_id,
        text=text,
        attachments=[keyboards.with_miniapp(kb, ctx.settings.miniapp_url)],
    )


async def _drop_buttons(ctx: AppContext, msg: Message | None) -> None:
    """Убрать кнопки у превью, чтобы по нему нельзя было нажать ещё раз."""
    if msg is None or not msg.mid or not msg.text:
        return  # без текста правка не нужна: повторное нажатие всё равно отсечёт dialog_states
    try:
        await ctx.max.edit_message(msg.mid, text=msg.text, attachments=[])
    except MaxApiError as e:
        log.info("cannot drop buttons from %s: %s", msg.mid, e)
