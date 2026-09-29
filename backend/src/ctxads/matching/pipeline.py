"""post → Proposal | None (+ причина). Всё, что требует LLM, — только здесь и в воркере."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ctxads.billing import pricing
from ctxads.context import AppContext
from ctxads.db.models import Channel, ChannelSettings, Post, Proposal, ProposalStatus
from ctxads.db.repo import ads as ads_repo
from ctxads.db.repo import channels as channels_repo
from ctxads.db.repo import posts as posts_repo
from ctxads.db.repo import proposals as proposals_repo
from ctxads.embeddings.base import blend
from ctxads.llm.base import LLMUnavailable
from ctxads.matching import explainer, retriever, scorer
from ctxads.matching.analyzer import AnalysisFailed, PostAnalysis, analyze_post, dump
from ctxads.matching.policy import (
    Candidate,
    ChannelRules,
    FrequencyRules,
    FrequencyState,
    Skip,
    filter_candidates,
    frequency_skip_reason,
)

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    proposal: Proposal | None
    reason: str
    analysis: PostAnalysis | None = None


async def _frequency_state(
    ctx: AppContext, s: AsyncSession, channel: Channel, post: Post
) -> FrequencyState:
    now = ctx.now()
    last_ad = await proposals_repo.last_placement_at(s, channel.chat_id)
    return FrequencyState(
        text_len=len(post.text.strip()),
        has_open_proposal=await proposals_repo.has_open(s, channel.chat_id),
        posts_since_last_ad=await posts_repo.count_regular_since(s, channel.chat_id, last_ad),
        minutes_since_last_ad=(now - last_ad).total_seconds() / 60 if last_ad else None,
        ads_last_24h=await proposals_repo.placements_since(
            s, channel.chat_id, now - timedelta(hours=24)
        ),
    )


def _frequency_rules(ctx: AppContext, cs: ChannelSettings) -> FrequencyRules:
    return FrequencyRules(
        min_posts_between_ads=cs.min_posts_between_ads,
        min_minutes_between_ads=cs.min_minutes_between_ads,
        max_ads_per_day=cs.max_ads_per_day,
        min_post_chars=ctx.settings.min_post_chars,
    )


def _channel_rules(channel: Channel, cs: ChannelSettings, context_category: str) -> ChannelRules:
    return ChannelRules(
        blocked_categories=frozenset(cs.blocked_categories or []),
        allowed_categories=(
            frozenset(cs.allowed_categories) if cs.allowed_categories is not None else None
        ),
        allow_regulated=frozenset(cs.allow_regulated or []),
        subscribers=channel.subscribers or 0,
        context_category=context_category,
    )


async def _get_analysis(ctx: AppContext, s: AsyncSession, post: Post) -> PostAnalysis:
    cached = await posts_repo.cached_analysis(s, post.text_hash)
    if cached is not None:
        return PostAnalysis.model_validate(cached)
    return await analyze_post(ctx.llm, post.text)


async def process_post(
    ctx: AppContext, s: AsyncSession, channel: Channel, post: Post
) -> PipelineResult:
    cs = await channels_repo.get_settings(s, channel.chat_id)

    reason = frequency_skip_reason(
        await _frequency_state(ctx, s, channel, post), _frequency_rules(ctx, cs)
    )
    if reason:
        return _skip(post, reason)

    try:
        analysis = await _get_analysis(ctx, s, post)
    except LLMUnavailable:
        return _skip(post, Skip.LLM_UNAVAILABLE)
    except AnalysisFailed:
        return _skip(post, Skip.ANALYSIS_INVALID)
    post.analysis = dump(analysis)

    if analysis.brand_safety == "unsafe":
        log.info(
            "post %s: brand_safety=unsafe (%s) — рекламу не предлагаем",
            post.mid,
            analysis.unsafe_reason,
        )
        return _skip(post, Skip.UNSAFE, analysis)

    post.embedding = await ctx.embedder.embed_query(analysis.query_text())
    channel.profile_embedding = blend(channel.profile_embedding, post.embedding)

    ranked = await _rank_candidates(ctx, s, channel, cs, post, analysis)
    if not ranked:
        return _skip(post, Skip.NO_MATCH, analysis)

    proposal = await _make_proposal(ctx, s, post, analysis, ranked[0], ranked[1:])
    return PipelineResult(proposal=proposal, reason="proposed", analysis=analysis)


async def _rank_candidates(
    ctx: AppContext,
    s: AsyncSession,
    channel: Channel,
    cs: ChannelSettings,
    post: Post,
    analysis: PostAnalysis,
) -> list[Candidate]:
    assert post.embedding is not None
    candidates = await retriever.retrieve(s, ctx.settings, ctx.embedder, channel, post.embedding)
    now = ctx.now()
    allowed = filter_candidates(
        candidates,
        _channel_rules(channel, cs, analysis.category),
        recently_rejected=await proposals_repo.rejected_ad_ids(
            s, channel.chat_id, now - timedelta(days=ctx.settings.reject_cooldown_days)
        ),
        recently_placed=await proposals_repo.placed_ad_ids(
            s, channel.chat_id, now - timedelta(days=ctx.settings.ad_frequency_cap_days)
        ),
    )
    return scorer.rank(allowed, ctx.settings.min_match_score)


async def _make_proposal(
    ctx: AppContext,
    s: AsyncSession,
    post: Post,
    analysis: PostAnalysis,
    best: Candidate,
    rest: list[Candidate],
) -> Proposal:
    ad = await ads_repo.get(s, best.ad_id)
    assert ad is not None
    reason = await explainer.explain(ctx.llm, analysis, post.text, ad.title, ad.body)
    proposal = Proposal(
        post_id=post.id,
        ad_id=ad.id,
        chat_id=post.chat_id,
        score=best.score,
        reason=reason,
        expected_payout=pricing.expected_channel_income(
            best.expected_gross, ctx.settings.platform_fee
        ),
        candidates=[
            {"ad_id": c.ad_id, "score": c.score, "gross": str(c.expected_gross)} for c in rest
        ],
        status=ProposalStatus.PENDING,
        expires_at=ctx.now() + timedelta(minutes=ctx.settings.proposal_ttl_min),
    )
    return await proposals_repo.add(s, proposal)


async def next_proposal(ctx: AppContext, s: AsyncSession, old: Proposal) -> Proposal | None:
    """Следующий кандидат из сохранённого топа — без повторного анализа поста."""
    post = await posts_repo.get(s, old.post_id)
    if post is None or post.analysis is None:
        return None
    if await proposals_repo.count_for_post(s, post.id) > ctx.settings.max_next_per_post:
        return None
    analysis = PostAnalysis.model_validate(post.analysis)
    rejected = await proposals_repo.rejected_ad_ids(
        s, old.chat_id, ctx.now() - timedelta(days=ctx.settings.reject_cooldown_days)
    )
    remaining = list(old.candidates or [])
    while remaining:
        item = remaining.pop(0)
        ad = await ads_repo.get(s, int(item["ad_id"]))
        if ad is None or ad.status != "active" or ad.budget_left <= 0 or ad.id in rejected:
            continue
        cs = await channels_repo.get_settings(s, old.chat_id)
        if ad.category in (cs.blocked_categories or []):
            continue
        best = Candidate(
            ad_id=ad.id,
            category=ad.category,
            sim_post=0.0,
            expected_gross=Decimal(item.get("gross", "0")),
            score=float(item.get("score", 0)),
        )
        rest = [
            Candidate(
                ad_id=int(i["ad_id"]),
                category="",
                sim_post=0.0,
                expected_gross=Decimal(i.get("gross", "0")),
                score=float(i.get("score", 0)),
            )
            for i in remaining
        ]
        return await _make_proposal(ctx, s, post, analysis, best, rest)
    return None


def _skip(post: Post, reason: str, analysis: PostAnalysis | None = None) -> PipelineResult:
    post.skip_reason = reason
    return PipelineResult(proposal=None, reason=reason, analysis=analysis)
