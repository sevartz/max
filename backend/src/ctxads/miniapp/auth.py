"""MAX WebApp initData validation. Never use initDataUnsafe or caller supplied IDs."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote_plus

MAX_INIT_DATA_AGE_SECONDS = 60 * 60
MAX_FUTURE_SKEW_SECONDS = 60
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MiniAppUser:
    user_id: int
    display_name: str | None


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_INIT_DATA_AGE_SECONDS,
) -> MiniAppUser | None:
    """Return the signed user only when the MAX signature and auth_date are valid."""
    if not init_data or not bot_token or len(init_data) > 16_384:
        return None
    pairs: list[tuple[str, str]] = []
    for item in init_data.split("&"):
        if not item or "=" not in item:
            return None
        key, raw_value = item.split("=", 1)
        if not key:
            return None
        try:
            if re.search(r"%(?![0-9a-fA-F]{2})", raw_value):
                return None
            value = unquote_plus(raw_value, errors="strict")
        except UnicodeDecodeError:
            return None
        pairs.append((key, value))

    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys) or keys.count("hash") != 1:
        return None
    values = dict(pairs)
    received_hash = values.pop("hash", "")
    if not _HEX_SHA256.fullmatch(received_hash):
        return None

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    try:
        auth_date = int(values["auth_date"])
        user_info = json.loads(values["user"])
        if not isinstance(user_info, dict):
            return None
        user_id = int(user_info["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if user_id <= 0:
        return None
    current_ts = (now or datetime.now(UTC)).timestamp()
    age = current_ts - auth_date
    if age < -MAX_FUTURE_SKEW_SECONDS or age > max_age_seconds:
        return None

    first = user_info.get("first_name")
    last = user_info.get("last_name")
    name = " ".join(
        part.strip() for part in (first, last) if isinstance(part, str) and part.strip()
    )
    return MiniAppUser(user_id=user_id, display_name=name or None)
