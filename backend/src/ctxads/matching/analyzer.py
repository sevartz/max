from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ctxads.llm.base import LLMClient, LLMUnavailable, extract_json
from ctxads.matching.categories import CATEGORIES

log = logging.getLogger(__name__)

SYSTEM = f"""Ты анализируешь посты каналов мессенджера MAX для подбора контекстной рекламы.
Верни СТРОГО один JSON-объект без пояснений:
{{"summary": str, "topics": [str], "category": str, "intent": str, "brand_safety": str,
"unsafe_reason": str|null}}
- summary: 1 предложение, о чём пост (по-русски, до 200 символов);
- topics: 1–5 ключевых тем в нижнем регистре;
- category: одна из {", ".join(CATEGORIES)};
- intent: "информационный" | "покупательский" | "развлекательный";
- brand_safety: "safe" | "sensitive" | "unsafe". unsafe — трагедии, катастрофы, смерть,
  насилие, политика, война. sensitive — болезни, личные драмы, спорные темы;
- unsafe_reason: кратко почему, если не safe, иначе null."""


class PostAnalysis(BaseModel):
    summary: str = Field(max_length=500)
    topics: list[str] = Field(default_factory=list)
    category: str = "other"
    intent: Literal["информационный", "покупательский", "развлекательный"] = "информационный"
    brand_safety: Literal["safe", "sensitive", "unsafe"] = "safe"
    unsafe_reason: str | None = None

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in CATEGORIES else "other"

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return [t.strip().lower() for t in v if t.strip()][:5]

    @field_validator("intent", mode="before")
    @classmethod
    def _intent(cls, v: object) -> object:
        allowed = ("информационный", "покупательский", "развлекательный")
        return v if v in allowed else "информационный"

    def query_text(self) -> str:
        return f"{self.summary} {' '.join(self.topics)}"


class AnalysisFailed(Exception):
    pass


async def analyze_post(llm: LLMClient, text: str) -> PostAnalysis:
    """1 LLM-вызов; при невалидном JSON — одна повторная попытка. LLMUnavailable пробрасываем."""
    user = f"ПОСТ:\n{text[:3000]}"
    last_error: Exception | None = None
    for _ in range(2):
        raw = await llm.chat(SYSTEM, user, temperature=0.2, max_tokens=400, json_mode=True)
        data = extract_json(raw)
        if data is None:
            last_error = ValueError("no json")
            continue
        try:
            return PostAnalysis.model_validate(data)
        except ValidationError as e:
            last_error = e
    log.warning("analysis invalid after retry: %s", last_error)
    raise AnalysisFailed(str(last_error))


def dump(analysis: PostAnalysis) -> dict[str, object]:
    return json.loads(analysis.model_dump_json())


__all__ = ["AnalysisFailed", "LLMUnavailable", "PostAnalysis", "analyze_post", "dump"]
