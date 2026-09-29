from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

DIM = 384


class Embedder(Protocol):
    dim: int
    # Типичный диапазон косинусов модели: ниже floor — «не похоже», выше ceil — «то же самое».
    # Нужен, чтобы порог MIN_MATCH_SCORE не зависел от выбранной модели.
    sim_floor: float
    sim_ceil: float

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


def normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else list(vec)


def cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def centroid(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    if not vectors:
        return None
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    return normalize([x / len(vectors) for x in acc])


def calibrate(sim: float, floor: float, ceil: float) -> float:
    if ceil <= floor:
        return sim
    return max(0.0, min(1.0, (sim - floor) / (ceil - floor)))


def blend(old: Sequence[float] | None, new: Sequence[float], weight: float = 0.1) -> list[float]:
    """Скользящее обновление профиля канала новым постом."""
    if old is None or len(old) == 0:
        return normalize(new)
    return normalize([(1 - weight) * o + weight * n for o, n in zip(old, new, strict=False)])
