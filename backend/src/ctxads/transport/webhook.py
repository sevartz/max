"""POST /webhook/max: проверить secret, дедуплицировать, положить в jobs, сразу 200."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ctxads.context import AppContext
from ctxads.transport.ingest import ingest

log = logging.getLogger(__name__)

router = APIRouter()

SECRET_HEADER = "X-Max-Bot-Api-Secret"

UPDATE_TYPES = [
    "bot_added",
    "bot_removed",
    "bot_started",
    "bot_stopped",
    "message_created",
    "message_edited",
    "message_removed",
    "message_callback",
    "bot_admin_permissions_changed",
]


@router.post("/webhook/max")
async def webhook(request: Request) -> dict[str, bool]:
    ctx: AppContext = request.app.state.ctx
    expected = ctx.settings.webhook_secret.get_secret_value()
    got = request.headers.get(SECRET_HEADER, "")
    if expected and not hmac.compare_digest(expected, got):
        raise HTTPException(status_code=401)
    try:
        raw = await request.json()
        await ingest(ctx.sessions, raw)
    except (ValueError, ValidationError):
        # Кривой апдейт не должен вызывать бесконечные ретраи со стороны MAX.
        log.warning("webhook: malformed update skipped")
    return {"ok": True}
