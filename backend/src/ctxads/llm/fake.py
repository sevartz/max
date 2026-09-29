"""FakeLLM: детерминированная эвристика для тестов и офлайн-демо. В сеть не ходит."""

from __future__ import annotations

import json
import re
from collections import Counter

from ctxads.matching.categories import KEYWORDS, UNSAFE_KEYWORDS, label


class FakeLLM:
    name = "fake"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({"system": system, "user": user, "json_mode": json_mode})
        if self.responses:
            return self.responses.pop(0)
        if "brand_safety" in system:
            return json.dumps(analyze(_section(user, "ПОСТ")), ensure_ascii=False)
        if "профиль канала" in system.lower():
            return profile(user)
        return explain(user)


def _section(text: str, name: str) -> str:
    m = re.search(rf"{name}:\s*(.*?)(?:\n[А-ЯA-Z_ ]+:|\Z)", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _hits(text: str) -> Counter[str]:
    low = f" {text.lower()} "
    counts: Counter[str] = Counter()
    for cat, stems in KEYWORDS.items():
        for stem in stems:
            if stem in low:
                counts[cat] += 1
    return counts


def _topic_words(text: str, categories: list[str]) -> list[str]:
    stems = [s.strip() for c in categories for s in KEYWORDS.get(c, ())]
    words: list[str] = []
    for word in re.findall(r"[а-яёa-z]+", text.lower()):
        if len(word) > 3 and any(word.startswith(s) for s in stems) and word not in words:
            words.append(word)
    return words[:5]


def analyze(text: str) -> dict[str, object]:
    low = text.lower()
    unsafe = [w for w in UNSAFE_KEYWORDS if w in low]
    hits = _hits(text)
    category = hits.most_common(1)[0][0] if hits else "other"
    top = [c for c, _ in hits.most_common(2)]
    topics = _topic_words(text, top) or [label(c).lower() for c in top] or ["разное"]
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return {
        "summary": first[:200],
        "topics": topics,
        "category": category,
        "intent": "информационный",
        "brand_safety": "unsafe" if unsafe else "safe",
        "unsafe_reason": f"чувствительная тема: {unsafe[0]}" if unsafe else None,
    }


def profile(user: str) -> str:
    hits = _hits(user)
    top = ", ".join(label(c).lower() for c, _ in hits.most_common(3)) or "разные темы"
    return f"Канал о темах: {top}."


def explain(user: str) -> str:
    post = _section(user, "ПОСТ")
    ad = _section(user, "ОБЪЯВЛЕНИЕ")
    ad_title = ad.splitlines()[0] if ad else "это предложение"
    topic = post.splitlines()[0][:80] if post else "тема поста"
    return f"Пост о теме «{topic}», а «{ad_title}» — естественный следующий шаг для читателя."
