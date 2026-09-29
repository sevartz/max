"""uv run python -m ctxads.seed — засеять каталог рекламы из seed/ads.yaml (идемпотентно)."""

from __future__ import annotations

import argparse
import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.config import get_settings
from ctxads.db.models import Ad, Advertiser
from ctxads.db.session import SessionFactory, make_engine, make_session_factory
from ctxads.embeddings import Embedder, build_embedder
from ctxads.matching.categories import CATEGORIES, label

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "seed" / "ads.yaml"


def ad_passage(title: str, body: str, category: str) -> str:
    return f"{title}. {body} Категория: {label(category)}."


async def seed(sessions: SessionFactory, embedder: Embedder, path: Path = DEFAULT_PATH) -> int:
    raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)
    ads = data["ads"]
    for ad in ads:
        if ad["category"] not in CATEGORIES:
            raise ValueError(f"{ad['key']}: unknown category {ad['category']}")
    vectors = await embedder.embed_passages(
        [ad_passage(a["title"], a["body"], a["category"]) for a in ads]
    )
    async with sessions.begin() as s:
        adv_ids = {a["key"]: await _upsert_advertiser(s, a) for a in data["advertisers"]}
        for ad, vec in zip(ads, vectors, strict=True):
            await _upsert_ad(s, ad, adv_ids[ad["advertiser"]], vec)
    return len(ads)


async def _upsert_advertiser(s: AsyncSession, a: dict[str, Any]) -> int:
    values = {k: str(a[k]) for k in ("key", "name", "legal_name", "inn", "postback_secret")}
    stmt = insert(Advertiser).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=[Advertiser.key], set_=values).returning(
        Advertiser.id
    )
    return int(await s.scalar(stmt))  # type: ignore[arg-type]


async def _upsert_ad(
    s: AsyncSession, a: dict[str, Any], advertiser_id: int, vec: list[float]
) -> None:
    budget = Decimal(str(a["budget"]))
    values = {
        "key": a["key"],
        "advertiser_id": advertiser_id,
        "title": a["title"],
        "body": a["body"],
        "url": a["url"],
        "image_url": a.get("image_url"),
        "category": a["category"],
        "erid": a["erid"],
        "pricing_model": a["pricing_model"],
        "price": Decimal(str(a["price"])),
        "budget_total": budget,
        "budget_left": budget,
        "targeting": a.get("targeting") or {},
        "status": "active",
        "embedding": vec,
    }
    # Повторный seed обновляет креатив и эмбеддинг, но не сбрасывает потраченный бюджет.
    update = {k: v for k, v in values.items() if k not in ("key", "budget_left", "status")}
    stmt = insert(Ad).values(**values).on_conflict_do_update(index_elements=[Ad.key], set_=update)
    await s.execute(stmt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    async def run() -> None:
        engine = make_engine(settings.database_url)
        n = await seed(make_session_factory(engine), build_embedder(settings), args.path)
        await engine.dispose()
        log.info("seeded %d ads (embedder=%s)", n, settings.embedder)

    asyncio.run(run())


if __name__ == "__main__":
    main()
