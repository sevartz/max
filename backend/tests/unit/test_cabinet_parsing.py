from __future__ import annotations

from decimal import Decimal

import pytest

from ctxads.ads import cabinet
from ctxads.ads.publisher import marking
from ctxads.db.models import Ad, Advertiser


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("250", Decimal("250.00")),
        ("1 500", Decimal("1500.00")),
        ("250,5", Decimal("250.50")),
        ("300 ₽", Decimal("300.00")),
        ("300 руб.", Decimal("300.00")),
        ("0", None),
        ("-5", None),
        ("abc", None),
        ("1.005", None),
        ("", None),
        ("999999999999", None),
    ],
)
def test_parse_money(raw, expected):
    assert cabinet.parse_money(raw) == expected


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ("https://example.com/shoes?utm=1", True),
        ("  http://shop.ru  ", True),
        ("example.com", False),
        ("ftp://example.com", False),
        ("https://exa mple.com", False),
        ("https://", False),
    ],
)
def test_parse_url(raw, ok):
    assert (cabinet.parse_url(raw) is not None) is ok


def test_parse_text_limits():
    assert cabinet.parse_text("  Кроссовки  ", 100) == "Кроссовки"
    assert cabinet.parse_text("   ", 100) is None
    assert cabinet.parse_text("x" * 101, 100) is None


def test_toggle_pause_and_top_up():
    ad = Ad(status=cabinet.ACTIVE, budget_total=Decimal(100), budget_left=Decimal(0))
    cabinet.toggle_pause(ad)
    assert ad.status == cabinet.PAUSED
    cabinet.toggle_pause(ad)
    assert ad.status == cabinet.ACTIVE
    cabinet.top_up(ad, Decimal(50))
    assert (ad.budget_total, ad.budget_left) == (Decimal(150), Decimal(50))


def test_demo_erid_and_key_are_unique_and_short():
    assert cabinet.demo_erid().startswith("DEMO-")
    assert cabinet.demo_erid() != cabinet.demo_erid()
    key = cabinet.new_ad_key(123456789)
    assert key.startswith("u123456789-") and len(key) <= 64


def test_marking_without_inn_for_cabinet_advertiser():
    ad = Ad(erid="DEMO-1")
    assert marking(Advertiser(legal_name="Бегун", inn=""), ad) == "#Реклама. Бегун. erid: DEMO-1"
    assert (
        marking(Advertiser(legal_name="ООО «Бегун»", inn="7700000002"), ad)
        == "#Реклама. ООО «Бегун», ИНН 7700000002. erid: DEMO-1"
    )
