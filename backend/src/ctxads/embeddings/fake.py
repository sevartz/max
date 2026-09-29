"""Детерминированные «эмбеддинги» по основам слов (hashing trick). Для тестов и офлайн-демо."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from ctxads.embeddings.base import DIM, normalize

_WORD = re.compile(r"[a-zа-яё0-9]+")
STEM_LEN = 5


def _bucket(token: str) -> tuple[int, float]:
    h = hashlib.md5(token.encode()).digest()
    return int.from_bytes(h[:4], "little") % DIM, 1.0 if h[4] & 1 else -1.0


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for word in _WORD.findall(text.lower().replace("ё", "е")):
        if len(word) < 3:
            continue
        idx, sign = _bucket(word[:STEM_LEN])
        vec[idx] += sign
    return normalize(vec)


class FakeEmbedder:
    dim = DIM
    sim_floor = 0.0
    sim_ceil = 0.5

    async def embed_query(self, text: str) -> list[float]:
        return embed(text)

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [embed(t) for t in texts]
