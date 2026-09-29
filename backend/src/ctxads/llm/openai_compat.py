"""OpenAI-совместимый провайдер. По умолчанию — NVIDIA NIM (integrate.api.nvidia.com/v1)."""

from __future__ import annotations

import logging
import re

import openai
from openai import AsyncOpenAI

from ctxads.llm.base import LLMUnavailable
from ctxads.llm.retry import RetryableLLMError, with_retries
from ctxads.ratelimit import RateLimiter

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OpenAICompatLLM:
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        rpm: int = 30,
        client: AsyncOpenAI | None = None,
        backoff_base: float = 1.0,
    ) -> None:
        if not model:
            raise ValueError("LLM_MODEL не задан: укажите модель из каталога build.nvidia.com")
        self._client = client or AsyncOpenAI(
            base_url=base_url, api_key=api_key, max_retries=0, timeout=60
        )
        self._model = model
        self._limiter = RateLimiter(rpm, 60.0)
        self._json_mode_supported = True
        self._backoff_base = backoff_base

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        async def call() -> str:
            await self._limiter.acquire()
            kwargs: dict = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                # Режим рассуждений выключен: шаблоны Qwen/DeepSeek на NIM понимают эти флаги,
                # остальные модели лишние ключи игнорируют.
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": False, "thinking": False}
                },
            }
            if json_mode and self._json_mode_supported:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                if "response_format" in kwargs and "response_format" in str(e):
                    log.info("model %s: response_format не поддерживается", self._model)
                    self._json_mode_supported = False
                    raise RetryableLLMError("response_format unsupported") from e
                raise LLMUnavailable(f"bad request: {e.status_code}") from e
            except openai.RateLimitError as e:
                raise RetryableLLMError("429") from e
            except openai.InternalServerError as e:
                raise RetryableLLMError(str(e.status_code)) from e
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                raise RetryableLLMError(type(e).__name__) from e
            except openai.APIStatusError as e:  # 401/402/403/404 — ретраи не помогут
                raise LLMUnavailable(f"status {e.status_code}") from e
            content = resp.choices[0].message.content or ""
            return _THINK_RE.sub("", content).strip()

        return await with_retries(call, name=self.name, backoff_base=self._backoff_base)
