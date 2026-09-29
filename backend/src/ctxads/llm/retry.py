from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ctxads.llm.base import LLMUnavailable

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class RetryableLLMError(Exception):
    pass


async def with_retries[T](
    fn: Callable[[], Awaitable[T]], *, name: str, backoff_base: float = 1.0
) -> T:
    """До 3 попыток с экспоненциальным backoff на 429/5xx/сеть; затем LLMUnavailable."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await fn()
        except RetryableLLMError as e:
            if attempt == MAX_ATTEMPTS:
                raise LLMUnavailable(f"{name}: {e}") from e
            delay = backoff_base * 2 ** (attempt - 1)
            log.warning("LLM %s: %s, retry in %.1fs", name, e, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
