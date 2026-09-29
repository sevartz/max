from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.db.models import Post


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


async def create(
    s: AsyncSession, *, chat_id: int, mid: str, text: str, created_at: datetime, is_our_ad: bool
) -> Post | None:
    """Создаёт пост; None, если пост с таким mid уже есть."""
    stmt = (
        insert(Post)
        .values(
            chat_id=chat_id,
            mid=mid,
            text=text,
            text_hash=text_hash(text),
            created_at=created_at,
            is_our_ad=is_our_ad,
        )
        .on_conflict_do_nothing(index_elements=[Post.mid])
        .returning(Post.id)
    )
    post_id = await s.scalar(stmt)
    return await s.get(Post, post_id) if post_id is not None else None


async def get(s: AsyncSession, post_id: int) -> Post | None:
    return await s.get(Post, post_id)


async def cached_analysis(s: AsyncSession, hash_: str) -> dict[str, Any] | None:
    q = select(Post.analysis).where(Post.text_hash == hash_, Post.analysis.is_not(None)).limit(1)
    return await s.scalar(q)


async def count_regular_since(s: AsyncSession, chat_id: int, since: datetime | None) -> int:
    q = select(func.count()).select_from(Post).where(Post.chat_id == chat_id, ~Post.is_our_ad)
    if since is not None:
        q = q.where(Post.created_at > since)
    return int(await s.scalar(q) or 0)
