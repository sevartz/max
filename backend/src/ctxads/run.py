"""CLI: uv run python -m ctxads.run --mode polling|webhook"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from ctxads.app import Mode, create_app
from ctxads.config import Settings, get_settings


def config_error(mode: Mode, settings: Settings) -> str | None:
    """Почему с таким конфигом стартовать нельзя (None — можно)."""
    if not settings.max_bot_token.get_secret_value():
        return "MAX_BOT_TOKEN не задан (см. .env.example)"
    if mode == "webhook":
        if not settings.webhook_public_url:
            return "WEBHOOK_PUBLIC_URL не задан: нужен для режима webhook (см. .env.example)"
        # Без секрета webhook принял бы поддельные апдейты от кого угодно (например, «Одобрить»).
        if not settings.webhook_secret.get_secret_value():
            return "WEBHOOK_SECRET не задан: без него webhook небезопасен (см. .env.example)"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="ctxads bot")
    parser.add_argument("--mode", choices=["polling", "webhook"], default="polling")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не светим токен и шум запросов
    settings = get_settings()
    if error := config_error(args.mode, settings):
        raise SystemExit(error)
    app = create_app(args.mode, settings)
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_level="info")


if __name__ == "__main__":
    main()
