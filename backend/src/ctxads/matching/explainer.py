from __future__ import annotations

import logging

from ctxads.llm.base import LLMClient, LLMUnavailable
from ctxads.matching.analyzer import PostAnalysis

log = logging.getLogger(__name__)

SYSTEM = """Объясни администратору канала в 1–2 коротких предложениях по-русски, почему это
рекламное объявление уместно именно под этим постом. Без приветствий, без кавычек вокруг ответа,
без обещаний дохода."""

PROFILE_SYSTEM = """Составь профиль канала: 1–2 предложения по-русски о тематике и аудитории
канала по его последним постам. Только текст профиля."""


async def explain(
    llm: LLMClient, analysis: PostAnalysis, post_text: str, ad_title: str, ad_body: str
) -> str:
    user = (
        f"ПОСТ:\n{post_text[:1200]}\n"
        f"РЕЗЮМЕ: {analysis.summary}\n"
        f"ОБЪЯВЛЕНИЕ:\n{ad_title}\n{ad_body[:600]}"
    )
    try:
        text = await llm.chat(SYSTEM, user, temperature=0.5, max_tokens=150)
    except LLMUnavailable:
        log.warning("explainer unavailable, using template")
        text = ""
    text = text.strip().strip('"«»')
    return text or f"Объявление близко к теме поста: {analysis.summary}"


async def channel_profile(llm: LLMClient, posts: list[str]) -> str | None:
    if not posts:
        return None
    user = "ПОСТЫ:\n" + "\n---\n".join(p[:400] for p in posts[:20])
    try:
        return (await llm.chat(PROFILE_SYSTEM, user, temperature=0.3, max_tokens=150)).strip()
    except LLMUnavailable:
        return None
