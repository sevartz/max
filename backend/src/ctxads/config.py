from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Один .env в корне репозитория: локально запускаем из backend/ (../.env), в Docker его
    # передаёт compose через env_file. .env рядом, если есть, приоритетнее.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    max_bot_token: SecretStr = SecretStr("")
    max_api_base: str = "https://platform-api2.max.ru"
    max_rps: float = 30.0
    max_chat_rps: float = 2.0

    webhook_public_url: str = ""
    webhook_secret: SecretStr = SecretStr("")
    public_base_url: str = "http://localhost:8000"
    miniapp_url: str = ""
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    database_url: str = "postgresql+asyncpg://ctx:ctx@localhost:5432/ctx"

    llm_provider: Literal["openai_compat", "yandexgpt", "gigachat", "fake"] = "openai_compat"
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_rpm: int = 30
    llm_fallback_provider: Literal["", "yandexgpt", "gigachat"] = ""
    llm_fallback_api_key: SecretStr = SecretStr("")
    llm_folder_id: str = ""

    embedder: Literal["e5_local", "fake"] = "e5_local"
    embedder_model: str = "intfloat/multilingual-e5-small"

    platform_fee: Decimal = Decimal("0.3")
    proposal_ttl_min: int = 120
    min_match_score: float = 0.35
    click_salt: SecretStr = SecretStr("change-me")

    consent_version: str = "2026-09"
    min_post_chars: int = 80
    max_next_per_post: int = 3
    reject_cooldown_days: int = 14
    ad_frequency_cap_days: int = 7
    views_poll_hours: int = 72
    worker_poll_interval_sec: float = 0.5
    job_max_attempts: int = 5
    # Грубые допущения для прогноза дохода в предложении админу.
    expected_reach: float = 0.3
    expected_ctr: float = 0.01
    expected_cr: float = 0.05

    @field_validator("llm_fallback_provider", mode="before")
    @classmethod
    def _empty_fallback(cls, v: object) -> object:
        return v or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
