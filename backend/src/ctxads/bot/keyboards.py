"""Inline-клавиатуры. Payload короткий: только id и действие, остальное — из БД."""

from __future__ import annotations

from typing import Any

from ctxads.bot import texts
from ctxads.matching.categories import CATEGORIES, REGULATED, label

Keyboard = dict[str, Any]


def callback(text: str, payload: str) -> dict[str, str]:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> dict[str, str]:
    return {"type": "link", "text": text, "url": url[:2048]}


def keyboard(rows: list[list[dict[str, str]]]) -> Keyboard:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def with_miniapp(kb: Keyboard, url: str) -> Keyboard:
    if not url:
        return kb
    rows = [list(row) for row in kb["payload"]["buttons"]]
    rows.append([{"type": "open_app", "text": "Открыть сервис", "web_app": url}])
    return keyboard(rows)


def miniapp(url: str) -> Keyboard | None:
    return with_miniapp(keyboard([]), url) if url else None


def consent() -> Keyboard:
    return keyboard([[callback(texts.CONSENT_BUTTON, "c:accept")]])


def proposal(proposal_id: int, category: str) -> Keyboard:
    p = f"p:{proposal_id}"
    return keyboard(
        [
            [
                callback(texts.BTN_APPROVE, f"{p}:approve"),
                callback(texts.BTN_REJECT, f"{p}:reject"),
            ],
            [callback(texts.BTN_NEXT, f"{p}:next")],
            [callback(texts.btn_block_category(category), f"{p}:block_cat")],
        ]
    )


def ad_link(url: str) -> Keyboard:
    return keyboard([[link(texts.AD_BUTTON, url)]])


def channels_list(channels: list[tuple[int, str]]) -> Keyboard:
    return keyboard([[callback(title, f"s:{chat_id}:open")] for chat_id, title in channels])


def channel_settings(
    chat_id: int, blocked: list[str], allow_regulated: list[str], paused: bool
) -> Keyboard:
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for cat in CATEGORIES:
        if cat == "other":
            continue
        regulated = cat in REGULATED
        text = texts.settings_category_button(
            cat, cat in blocked, regulated, cat in allow_regulated
        )
        row.append(callback(text, f"s:{chat_id}:{'r' if regulated else 'b'}:{cat}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([callback(texts.BTN_RESUME if paused else texts.BTN_PAUSE, f"s:{chat_id}:pause")])
    rows.append([callback(texts.BTN_BACK, "s:0:list")])
    return keyboard(rows)


# --- Кабинет рекламодателя: payload "a:<действие>[:<id>[:<поле>]]" ---


def _pairs(buttons: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def cabinet_menu(ads: list[tuple[int, str]]) -> Keyboard:
    rows = [[callback(text, f"a:ad:{ad_id}")] for ad_id, text in ads]
    rows.append([callback(texts.BTN_CAB_NEW, "a:new")])
    rows.append([callback(texts.BTN_CAB_STATS, "a:stats")])
    return keyboard(rows)


def cabinet_categories(categories: tuple[str, ...]) -> Keyboard:
    return keyboard(_pairs([callback(label(c), f"a:cat:{c}") for c in categories]))


def cabinet_pricing_models(models: tuple[str, ...]) -> Keyboard:
    return keyboard([[callback(texts.cab_pricing_button(m), f"a:pm:{m}")] for m in models])


def cabinet_confirm() -> Keyboard:
    return keyboard(
        [[callback(texts.BTN_CAB_LAUNCH, "a:go"), callback(texts.BTN_CAB_CANCEL, "a:cancel")]]
    )


def cabinet_ad(ad_id: int, paused: bool) -> Keyboard:
    edit = [callback(t, f"a:ed:{ad_id}:{field}") for field, t in texts.BTN_CAB_EDIT.items()]
    edit.append(callback(texts.BTN_CAB_TOPUP, f"a:top:{ad_id}"))
    rows = _pairs(edit)
    rows.append([callback(texts.BTN_RESUME if paused else texts.BTN_PAUSE, f"a:pause:{ad_id}")])
    rows.append([callback(texts.BTN_CAB_BACK, "a:menu")])
    return keyboard(rows)
