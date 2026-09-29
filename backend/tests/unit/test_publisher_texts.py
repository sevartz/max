from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ctxads.ads.publisher import build_ad_post, button_url, is_public_base_url
from ctxads.bot import keyboards, texts
from ctxads.bot.handlers.callbacks import parse_proposal_payload
from ctxads.db.models import Ad, Advertiser, Placement


def _ad(**kw) -> Ad:
    base = dict(
        title="ЛабТест — анализ на глюкозу",
        body="Скидка 20%.",
        url="https://example.com",
        category="medicine",
        erid="DEMO-xxxx",
        pricing_model="cpm",
        price=Decimal(250),
    )
    base.update(kw)
    return Ad(**base)


ADV = Advertiser(name="Ромашка", legal_name="ООО «Ромашка»", inn="7700000000")


def test_ad_post_has_legal_marking_and_tracker_button():
    text, attachments = build_ad_post(_ad(), ADV, "https://ads.test/r/tok")
    assert text.endswith("#Реклама. ООО «Ромашка», ИНН 7700000000. erid: DEMO-xxxx")
    assert text.startswith("ЛабТест — анализ на глюкозу\n\nСкидка 20%.")
    assert attachments == [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "link", "text": "Подробнее", "url": "https://ads.test/r/tok"}]
                ]
            },
        }
    ]


def test_ad_post_with_image():
    _, attachments = build_ad_post(_ad(image_url="https://img/x.png"), ADV, "https://t")
    assert attachments[0] == {"type": "image", "payload": {"url": "https://img/x.png"}}


def test_proposal_payloads_are_short():
    kb = keyboards.proposal(123456, "medicine")
    payloads = [b["payload"] for row in kb["payload"]["buttons"] for b in row]
    assert payloads == [
        "p:123456:approve",
        "p:123456:reject",
        "p:123456:next",
        "p:123456:block_cat",
    ]
    assert all(len(p) <= 32 for p in payloads)
    labels = [b["text"] for row in kb["payload"]["buttons"] for b in row]
    assert "🚫 Не предлагать «Медицина»" in labels


def test_parse_payload():
    assert parse_proposal_payload("p:5:approve") == (5, "approve")
    assert parse_proposal_payload("p:5:drop_table") is None
    assert parse_proposal_payload("p:x:approve") is None
    assert parse_proposal_payload("p:5") is None


def test_proposal_text_matches_spec_shape():
    text = texts.proposal(
        post_text="Бургер и сахар в крови: что происходит после фастфуда",
        channel_title="ЗОЖ",
        ad_title="ЛабТест — анализ на глюкозу",
        reason="пост о сахаре",
        pricing_model="cpm",
        price=Decimal(250),
        expected=Decimal("180.4"),
        expires_at=datetime(2026, 9, 25, 11, 32, tzinfo=UTC),
    )
    assert "📌 Новый пост" in text and "💡 Предлагаем: ЛабТест" in text
    assert "🧠 Почему: пост о сахаре" in text
    assert "💰 Модель: CPM 250 ₽ за 1000 показов · ожидаемо ~180 ₽" in text
    assert "до 14:32" in text, "время показываем по Москве"


def test_rub_format():
    assert texts.rub(Decimal("12345.6")) == "12 346 ₽"
    assert texts.rub(Decimal("3.5")) == "3.50 ₽"
    assert texts.rub(0) == "0.00 ₽"


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://ads.example.com", True),
        ("http://8.8.8.8:8000", True),
        ("http://localhost:8000", False),
        ("http://127.0.0.1:8000", False),
        ("http://192.168.1.5", False),
        ("http://bot.local", False),
        ("", False),
    ],
)
def test_is_public_base_url(url, public):
    assert is_public_base_url(url) is public


def _ctx(base_url: str):
    return SimpleNamespace(settings=SimpleNamespace(public_base_url=base_url))


def test_button_url_uses_tracker_only_with_public_address():
    ad = _ad(key="tours", url="https://t.me/stbuk")
    placement = Placement(token="tok", chat_id=-7001)
    assert button_url(_ctx("https://ads.test/"), ad, placement) == "https://ads.test/r/tok"
    direct = button_url(_ctx("http://localhost:8000"), ad, placement)
    assert direct.startswith("https://t.me/stbuk?") and "utm_campaign=tours" in direct
    assert "utm_content=-7001" in direct and "localhost" not in direct
