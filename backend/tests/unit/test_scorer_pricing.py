from __future__ import annotations

from decimal import Decimal

import pytest

from ctxads.billing import pricing
from ctxads.embeddings.base import calibrate
from ctxads.matching.policy import Candidate
from ctxads.matching.scorer import rank, score


def test_score_weights():
    assert score(1, 1, 1) == pytest.approx(1.0)
    assert score(1, 0, 0) == pytest.approx(0.6)
    assert score(0, 1, 0) == pytest.approx(0.25)
    assert score(0, 0, 1) == pytest.approx(0.15)
    assert score(-0.5, 2, 0) == pytest.approx(0.25), "значения зажаты в [0, 1]"


def test_rank_threshold_and_order():
    cands = [
        Candidate(ad_id=1, category="x", sim_post=0.3, expected_gross=Decimal(100)),
        Candidate(ad_id=2, category="x", sim_post=0.9, sim_channel=0.5, expected_gross=Decimal(50)),
        Candidate(ad_id=3, category="x", sim_post=0.1, expected_gross=Decimal(0)),
    ]
    ranked = rank(cands, min_score=0.35)
    assert [c.ad_id for c in ranked] == [2]
    assert ranked[0].score == pytest.approx(0.6 * 0.9 + 0.25 * 0.5 + 0.15 * 0.5)
    assert rank([], 0.35) == []


def test_calibrate():
    assert calibrate(0.78, 0.78, 0.9) == 0
    assert calibrate(0.84, 0.78, 0.9) == pytest.approx(0.5)
    assert calibrate(0.95, 0.78, 0.9) == 1


def test_split_is_exact_to_the_kopeck():
    s = pricing.split(Decimal("100.01"), Decimal("0.3"))
    assert s.advertiser_debit == s.channel_credit + s.platform_fee
    assert s.platform_fee == Decimal("30.00")


def test_charges():
    assert pricing.cpm_charge(Decimal(250), 1500) == Decimal("375.00")
    assert pricing.cpm_charge(Decimal(250), 0) == 0
    assert pricing.cpc_charge(Decimal("18.5")) == Decimal("18.50")
    assert pricing.cpa_charge(Decimal(150)) == Decimal("150.00")
    assert pricing.cpa_charge(Decimal("0.1"), Decimal(2000)) == Decimal("200.00"), "доля от чека"


def test_expected_gross():
    kw = dict(reach=0.3, ctr=0.01, cr=0.05)
    assert pricing.expected_gross("cpm", Decimal(250), 10000, **kw) == Decimal("750.00")
    assert pricing.expected_gross("cpc", Decimal(20), 10000, **kw) == Decimal("600.00")
    assert pricing.expected_gross("cpa", Decimal(500), 10000, **kw) == Decimal("750.00")
