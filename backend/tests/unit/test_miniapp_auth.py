from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import quote_plus

from ctxads.miniapp.auth import validate_init_data

TOKEN = "test-token"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def signed_data(**overrides: str) -> str:
    values = {
        "auth_date": str(int(NOW.timestamp())),
        "user": json.dumps(
            {"id": 123, "first_name": "Анна", "last_name": "Иванова"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "query_id": "query-123",
    }
    values.update(overrides)
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return (
        "&".join(f"{key}={quote_plus(value)}" for key, value in values.items()) + f"&hash={digest}"
    )


def test_valid_signature_extracts_user() -> None:
    assert validate_init_data(signed_data(), TOKEN, now=NOW).user_id == 123
    assert validate_init_data(signed_data(), TOKEN, now=NOW).display_name == "Анна Иванова"


def test_rejects_tampered_and_duplicate_fields() -> None:
    assert (
        validate_init_data(signed_data().replace("query-123", "query-456"), TOKEN, now=NOW) is None
    )
    assert validate_init_data(signed_data() + "&auth_date=1", TOKEN, now=NOW) is None


def test_rejects_expired_and_future_auth_dates() -> None:
    old = str(int(NOW.timestamp()) - 3_601)
    future = str(int(NOW.timestamp()) + 61)
    assert validate_init_data(signed_data(auth_date=old), TOKEN, now=NOW) is None
    assert validate_init_data(signed_data(auth_date=future), TOKEN, now=NOW) is None


def test_rejects_malformed_query_and_non_object_user() -> None:
    assert validate_init_data("user=%ZZ&auth_date=1&hash=" + "0" * 64, TOKEN, now=NOW) is None
    assert validate_init_data(signed_data(user="[]"), TOKEN, now=NOW) is None
