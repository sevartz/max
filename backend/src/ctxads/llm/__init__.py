from __future__ import annotations

from ctxads.config import Settings
from ctxads.llm.base import FallbackLLM, LLMClient, LLMUnavailable


def _provider(settings: Settings, name: str, api_key: str) -> LLMClient:
    if name == "fake":
        from ctxads.llm.fake import FakeLLM

        return FakeLLM()
    if name == "openai_compat":
        from ctxads.llm.openai_compat import OpenAICompatLLM

        return OpenAICompatLLM(
            base_url=settings.llm_base_url,
            api_key=api_key,
            model=settings.llm_model,
            rpm=settings.llm_rpm,
        )
    if name == "yandexgpt":
        from ctxads.llm.yandexgpt import YandexGPTLLM

        return YandexGPTLLM(api_key=api_key, folder_id=settings.llm_folder_id)
    if name == "gigachat":
        from ctxads.llm.gigachat import GigaChatLLM

        return GigaChatLLM(auth_key=api_key)
    raise ValueError(f"unknown LLM provider: {name}")


def build_llm(settings: Settings) -> LLMClient:
    primary = _provider(settings, settings.llm_provider, settings.llm_api_key.get_secret_value())
    if settings.llm_fallback_provider:
        fallback = _provider(
            settings,
            settings.llm_fallback_provider,
            settings.llm_fallback_api_key.get_secret_value(),
        )
        return FallbackLLM(primary, fallback)
    return primary


__all__ = ["LLMClient", "LLMUnavailable", "build_llm"]
