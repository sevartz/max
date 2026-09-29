from __future__ import annotations

from ctxads.config import Settings
from ctxads.embeddings.base import Embedder


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        from ctxads.embeddings.fake import FakeEmbedder

        return FakeEmbedder()
    from ctxads.embeddings.e5_local import E5LocalEmbedder

    return E5LocalEmbedder(settings.embedder_model)


__all__ = ["Embedder", "build_embedder"]
