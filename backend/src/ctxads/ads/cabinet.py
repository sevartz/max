"""Кабинет рекламодателя: разбор ввода и операции над своими объявлениями (без платежей)."""

from __future__ import annotations

import re
import secrets
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Ad, Advertiser
from ctxads.db.repo import ads as ads_repo
from ctxads.embeddings.base import Embedder
from ctxads.matching.categories import CATEGORIES
from ctxads.seed import ad_passage

ACTIVE, PAUSED = "active", "paused"
PRICING_MODELS = ("cpm", "cpc", "cpa")
CATEGORY_CHOICES = tuple(c for c in CATEGORIES if c != "other")

NAME_MAX = 100
TITLE_MAX = 100
BODY_MAX = 1000
URL_MAX = 2048
MONEY_MAX = Decimal("100000000")

EDITABLE_TEXT_FIELDS = ("title", "body", "url", "price")


def parse_text(value: str, max_len: int) -> str | None:
    text = value.strip()
    return text if 0 < len(text) <= max_len else None


def parse_url(value: str) -> str | None:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in url:
        return None
    return url if len(url) <= URL_MAX else None


def parse_money(value: str, minimum: Decimal = Decimal("0.01")) -> Decimal | None:
    """«1 500», «250,5», «300 ₽» → Decimal с копейками; None, если не число или вне границ."""
    cleaned = re.sub(r"[\s₽рубРУБ.]*$", "", value.strip())
    cleaned = cleaned.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < minimum or amount > MONEY_MAX:
        return None
    if amount != amount.quantize(Decimal("0.01")):
        return None
    return amount.quantize(Decimal("0.01"))


def new_ad_key(user_id: int) -> str:
    return f"u{user_id}-{secrets.token_hex(4)}"


def demo_erid() -> str:
    # ОРД в MVP не подключён — как и в seed, ERID демонстрационный.
    return f"DEMO-{secrets.token_hex(5).upper()}"


async def create_advertiser(s: AsyncSession, user_id: int, name: str) -> Advertiser:
    return await ads_repo.add_advertiser(
        s,
        Advertiser(
            key=f"u{user_id}",
            name=name,
            legal_name=name,
            inn="",
            postback_secret=secrets.token_hex(16),
            owner_user_id=user_id,
        ),
    )


async def embed_ad(embedder: Embedder, title: str, body: str, category: str) -> list[float]:
    [vec] = await embedder.embed_passages([ad_passage(title, body, category)])
    return vec


async def create_ad(
    s: AsyncSession, embedder: Embedder, advertiser: Advertiser, draft: dict[str, Any]
) -> Ad:
    budget = Decimal(draft["budget"])
    return await ads_repo.add(
        s,
        Ad(
            key=new_ad_key(advertiser.owner_user_id or advertiser.id),
            advertiser_id=advertiser.id,
            title=draft["title"],
            body=draft["body"],
            url=draft["url"],
            category=draft["category"],
            erid=demo_erid(),
            pricing_model=draft["pricing_model"],
            price=Decimal(draft["price"]),
            budget_total=budget,
            budget_left=budget,
            targeting={},
            status=ACTIVE,
            embedding=await embed_ad(embedder, draft["title"], draft["body"], draft["category"]),
        ),
    )


async def update_field(embedder: Embedder, ad: Ad, field: str, value: str | Decimal) -> None:
    """Меняет поле; при смене смысла объявления пересчитывает эмбеддинг для подбора."""
    setattr(ad, field, value)
    if field in ("title", "body", "category"):
        ad.embedding = await embed_ad(embedder, ad.title, ad.body, ad.category)


def toggle_pause(ad: Ad) -> None:
    set_paused(ad, ad.status == ACTIVE)


def set_paused(ad: Ad, paused: bool) -> None:
    ad.status = PAUSED if paused else ACTIVE


def top_up(ad: Ad, amount: Decimal) -> None:
    """Демо-пополнение: реальных платежей в MVP нет, меняются только цифры бюджета."""
    ad.budget_total += amount
    ad.budget_left += amount
