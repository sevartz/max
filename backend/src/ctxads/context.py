from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ctxads.config import Settings
from ctxads.db.session import SessionFactory
from ctxads.embeddings.base import Embedder
from ctxads.llm.base import LLMClient
from ctxads.max_api.client import MaxClient


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppContext:
    """Всё, что нужно хендлерам и задачам. Внешние сервисы — за интерфейсами."""

    settings: Settings
    max: MaxClient
    sessions: SessionFactory
    llm: LLMClient
    embedder: Embedder
    bot_user_id: int | None = None
    clock: Callable[[], datetime] = field(default=utcnow)

    def now(self) -> datetime:
        return self.clock()
