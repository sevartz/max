from __future__ import annotations

import json
from typing import Protocol


class LLMUnavailable(Exception):
    """LLM не ответила после ретраев (сеть, 429, 5xx, кончились кредиты)."""


class LLMClient(Protocol):
    name: str

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str: ...


def extract_json(text: str) -> dict | None:
    """Первый JSON-объект из текста (модели любят оборачивать ответ в ```json … ```)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


class FallbackLLM:
    """Основной провайдер, при LLMUnavailable — запасной (например, YandexGPT)."""

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        try:
            return await self.primary.chat(
                system, user, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
            )
        except LLMUnavailable:
            return await self.fallback.chat(
                system, user, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
            )
