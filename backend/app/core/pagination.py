"""Opaque, tamper-evident keyset cursors."""

import base64
import json
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import constant_time_equals, sign


class InvalidCursor(AppError):
    code = "invalid_cursor"


def encode_cursor(data: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":"), default=str).encode()).decode()
    mac = sign(body, get_settings().cursor_secret.get_secret_value())[:24]
    return f"{body}.{mac}"


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        body, mac = cursor.rsplit(".", 1)
    except ValueError as exc:
        raise InvalidCursor("Malformed cursor") from exc
    expected = sign(body, get_settings().cursor_secret.get_secret_value())[:24]
    if not constant_time_equals(mac, expected):
        raise InvalidCursor("Cursor signature mismatch")
    try:
        return json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursor("Malformed cursor") from exc
