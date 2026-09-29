from __future__ import annotations

from decimal import Decimal

import pytest

from ctxads.matching.policy import (
    Candidate,
    ChannelRules,
    FrequencyRules,
    FrequencyState,
    Skip,
    filter_candidates,
    frequency_skip_reason,
    rejection_reason,
)

RULES = FrequencyRules(min_posts_between_ads=3, min_minutes_between_ads=120, max_ads_per_day=3)


def state(**kw) -> FrequencyState:
    base = dict(
        text_len=200,
        has_open_proposal=False,
        posts_since_last_ad=5,
        minutes_since_last_ad=500.0,
        ads_last_24h=0,
    )
    base.update(kw)
    return FrequencyState(**base)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, None),
        ({"text_len": 79}, Skip.TOO_SHORT),
        ({"has_open_proposal": True}, Skip.OPEN_PROPOSAL),
        ({"posts_since_last_ad": 2}, Skip.TOO_FEW_POSTS),
        ({"posts_since_last_ad": 3}, None),
        ({"minutes_since_last_ad": 119.0}, Skip.TOO_SOON),
        ({"ads_last_24h": 3}, Skip.DAILY_LIMIT),
        ({"minutes_since_last_ad": None, "posts_since_last_ad": 0}, None),
    ],
)
def test_frequency(kw, expected):
    assert frequency_skip_reason(state(**kw), RULES) == expected


def cand(**kw) -> Candidate:
    base = dict(ad_id=1, category="sport", sim_post=0.8)
    base.update(kw)
    return Candidate(**base)


def reason(c: Candidate, rules: ChannelRules | None = None, **kw) -> str | None:
    return rejection_reason(
        c,
        rules or ChannelRules(subscribers=1000),
        recently_rejected=kw.get("rejected", set()),
        recently_placed=kw.get("placed", set()),
    )


def test_candidate_passes_by_default():
    assert reason(cand()) is None


def test_blocked_and_allowlist():
    assert reason(cand(), ChannelRules(blocked_categories=frozenset({"sport"}))) == (
        "blocked_category"
    )
    rules = ChannelRules(allowed_categories=frozenset({"food"}))
    assert reason(cand(), rules) == "not_allowed_category"


@pytest.mark.parametrize("category", ["betting", "alcohol", "finance", "medicine"])
def test_regulated_requires_explicit_allow(category):
    assert reason(cand(category=category)) == "regulated"
    rules = ChannelRules(allow_regulated=frozenset({category}), subscribers=1000)
    assert reason(cand(category=category), rules) is None


def test_targeting():
    c = cand(targeting={"min_subscribers": 5000})
    assert reason(c) == "targeting_subscribers"
    c = cand(targeting={"channel_categories": ["tech"]})
    assert reason(c, ChannelRules(context_category="sport")) == "targeting_category"
    assert reason(c, ChannelRules(context_category="tech")) is None


def test_budget_rejections_and_frequency_cap():
    assert reason(cand(budget_left=Decimal(0))) == "inactive"
    assert reason(cand(status="paused")) == "inactive"
    assert reason(cand(), rejected={1}) == "recently_rejected"
    assert reason(cand(), placed={1}) == "frequency_cap"


def test_filter_keeps_order():
    items = [cand(ad_id=1), cand(ad_id=2, category="betting"), cand(ad_id=3)]
    kept = filter_candidates(items, ChannelRules(), recently_rejected=set(), recently_placed=set())
    assert [c.ad_id for c in kept] == [1, 3]
