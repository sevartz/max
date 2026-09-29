"""Локальная intfloat/multilingual-e5-small (dim 384): кредиты NIM на эмбеддинги не тратим."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import Any


class E5LocalEmbedder:
    dim = 384
    # У e5 даже несвязанные русские тексты дают косинус ~0.77 (медиана на seed-каталоге),
    # а точные попадания — 0.84–0.88. См. README, раздел «Калибровка».
    sim_floor = 0.78
    sim_ceil = 0.88

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name, device="cpu")
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self._load().encode(texts, normalize_embeddings=True, batch_size=16)
        return [v.tolist() for v in vecs]

    async def embed_query(self, text: str) -> list[float]:
        # e5 требует префиксы "query: " / "passage: ".
        return (await asyncio.to_thread(self._encode, [f"query: {text}"]))[0]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, [f"passage: {t}" for t in texts])

    async def warmup(self) -> None:
        await asyncio.to_thread(self._load)
