"""Скоринг без LLM: предсказуемо, дёшево, тестируемо."""

from __future__ import annotations

from ctxads.matching.policy import Candidate

W_POST, W_CHANNEL, W_PAYOUT = 0.6, 0.25, 0.15


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(sim_post: float, sim_channel: float, payout_norm: float) -> float:
    return (
        W_POST * _clamp(sim_post) + W_CHANNEL * _clamp(sim_channel) + W_PAYOUT * _clamp(payout_norm)
    )


def rank(candidates: list[Candidate], min_score: float) -> list[Candidate]:
    """Проставляет score, отбрасывает всё ниже порога, сортирует по убыванию."""
    if not candidates:
        return []
    top_payout = max(c.expected_gross for c in candidates)
    for c in candidates:
        norm = float(c.expected_gross / top_payout) if top_payout > 0 else 0.0
        c.score = round(score(c.sim_post, c.sim_channel, norm), 4)
    passed = [c for c in candidates if c.score >= min_score]
    return sorted(passed, key=lambda c: c.score, reverse=True)
