from __future__ import annotations

from ctxads.config import Settings
from ctxads.run import config_error


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "max_bot_token": "t",
        "webhook_public_url": "https://bot.example/webhook/max",
        "webhook_secret": "s3cret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_ok_configs():
    assert config_error("webhook", make()) is None
    assert config_error("polling", make(webhook_public_url="", webhook_secret="")) is None


def test_token_required_in_any_mode():
    assert "MAX_BOT_TOKEN" in (config_error("polling", make(max_bot_token="")) or "")
    assert "MAX_BOT_TOKEN" in (config_error("webhook", make(max_bot_token="")) or "")


def test_webhook_requires_url_and_secret():
    assert "WEBHOOK_PUBLIC_URL" in (config_error("webhook", make(webhook_public_url="")) or "")
    assert "WEBHOOK_SECRET" in (config_error("webhook", make(webhook_secret="")) or "")
