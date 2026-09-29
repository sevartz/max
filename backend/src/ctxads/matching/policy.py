"""Правила размещения. Чистые функции над снимками состояния — без БД и сети."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ctxads.matching.categories import REGULATED


class Skip:
    TOO_SHORT = "too_short"
    OPEN_PROPOSAL = "open_proposal"
    TOO_FEW_POSTS = "too_few_posts"
    TOO_SOON = "too_soon"
    DAILY_LIMIT = "daily_limit"
    LLM_UNAVAILABLE = "llm_unavailable"
    ANALYSIS_INVALID = "analysis_invalid"
    UNSAFE = "unsafe"
    NO_MATCH = "no_match"
    CHANNEL_INACTIVE = "channel_inactive"


@dataclass(frozen=True)
class FrequencyRules:
    min_posts_between_ads: int = 3
    min_minutes_between_ads: int = 120
    max_ads_per_day: int = 3
    min_post_chars: int = 80


@dataclass(frozen=True)
class FrequencyState:
    text_len: int
    has_open_proposal: bool
    posts_since_last_ad: int  # включая текущий пост
    minutes_since_last_ad: float | None  # None — рекламы в канале ещё не было
    ads_last_24h: int


def frequency_skip_reason(state: FrequencyState, rules: FrequencyRules) -> str | None:
    """Проверки до LLM, чтобы не тратить вызовы."""
    if state.text_len < rules.min_post_chars:
        return Skip.TOO_SHORT
    if state.has_open_proposal:
        return Skip.OPEN_PROPOSAL
    if state.minutes_since_last_ad is not None:
        if state.posts_since_last_ad < rules.min_posts_between_ads:
            return Skip.TOO_FEW_POSTS
        if state.minutes_since_last_ad < rules.min_minutes_between_ads:
            return Skip.TOO_SOON
    if state.ads_last_24h >= rules.max_ads_per_day:
        return Skip.DAILY_LIMIT
    return None


@dataclass(frozen=True)
class ChannelRules:
    blocked_categories: frozenset[str] = frozenset()
    allowed_categories: frozenset[str] | None = None
    allow_regulated: frozenset[str] = frozenset()
    subscribers: int = 0
    context_category: str = "other"


@dataclass
class Candidate:
    ad_id: int
    category: str
    sim_post: float
    sim_channel: float = 0.0
    expected_gross: Decimal = Decimal(0)
    targeting: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    budget_left: Decimal = Decimal(1)
    score: float = 0.0


def rejection_reason(
    c: Candidate,
    rules: ChannelRules,
    *,
    recently_rejected: set[int],
    recently_placed: set[int],
) -> str | None:
    if c.status != "active" or c.budget_left <= 0:
        return "inactive"
    if c.category in rules.blocked_categories:
        return "blocked_category"
    if rules.allowed_categories is not None and c.category not in rules.allowed_categories:
        return "not_allowed_category"
    if c.category in REGULATED and c.category not in rules.allow_regulated:
        return "regulated"
    allowed_ctx = c.targeting.get("channel_categories")
    if allowed_ctx and rules.context_category not in allowed_ctx:
        return "targeting_category"
    if rules.subscribers < int(c.targeting.get("min_subscribers", 0) or 0):
        return "targeting_subscribers"
    if c.ad_id in recently_rejected:
        return "recently_rejected"
    if c.ad_id in recently_placed:
        return "frequency_cap"
    return None


def filter_candidates(
    candidates: list[Candidate],
    rules: ChannelRules,
    *,
    recently_rejected: set[int],
    recently_placed: set[int],
) -> list[Candidate]:
    return [
        c
        for c in candidates
        if rejection_reason(
            c, rules, recently_rejected=recently_rejected, recently_placed=recently_placed
        )
        is None
    ]
