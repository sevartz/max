"""Чистые функции ценообразования: без БД, легко тестировать."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Split:
    advertiser_debit: Decimal
    channel_credit: Decimal
    platform_fee: Decimal


def split(amount: Decimal, fee_rate: Decimal) -> Split:
    """Списание с рекламодателя = доход канала + комиссия платформы (копейка в копейку)."""
    debit = money(amount)
    fee = money(debit * fee_rate)
    return Split(advertiser_debit=debit, channel_credit=debit - fee, platform_fee=fee)


def cpm_charge(price_per_1000: Decimal, delta_views: int) -> Decimal:
    if delta_views <= 0:
        return Decimal(0)
    return money(price_per_1000 * delta_views / 1000)


def cpc_charge(price: Decimal) -> Decimal:
    return money(price)


def cpa_charge(price: Decimal, conversion_amount: Decimal | None = None) -> Decimal:
    """CPA: фикс `price`; если price < 1 — это доля от суммы конверсии (реферальная модель)."""
    if price < 1 and conversion_amount is not None:
        return money(price * conversion_amount)
    return money(price)


def expected_gross(
    pricing_model: str,
    price: Decimal,
    subscribers: int,
    *,
    reach: float,
    ctr: float,
    cr: float,
) -> Decimal:
    """Грубый прогноз списания с рекламодателя за одно размещение."""
    views = Decimal(max(subscribers, 0)) * Decimal(str(reach))
    if pricing_model == "cpm":
        return money(price * views / 1000)
    clicks = views * Decimal(str(ctr))
    if pricing_model == "cpc":
        return money(price * clicks)
    if pricing_model == "cpa":
        return money(price * clicks * Decimal(str(cr)))
    return Decimal(0)


def expected_channel_income(gross: Decimal, fee_rate: Decimal) -> Decimal:
    return split(gross, fee_rate).channel_credit
