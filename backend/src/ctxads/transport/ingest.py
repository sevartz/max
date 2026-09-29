from __future__ import annotations

from typing import Any

from ctxads.db.repo import updates as updates_repo
from ctxads.db.session import SessionFactory
from ctxads.jobs import queue
from ctxads.max_api.models import Update


async def ingest(sessions: SessionFactory, raw: dict[str, Any]) -> bool:
    """Дедупликация + постановка в очередь. Никакой логики и сети. True — апдейт новый."""
    update = Update.model_validate(raw)
    async with sessions.begin() as s:
        if not await updates_repo.mark_processed(s, update.dedupe_key()):
            return False
        await queue.enqueue(s, "update", raw)
    return True
