"""Security audit trail. Never record secrets, tokens or customer PII in ``details``."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

_REDACT = {"password", "token", "secret", "authorization", "refresh_token", "access_token", "code"}


def _scrub(details: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[redacted]" if k.lower() in _REDACT else v) for k, v in details.items()}


def record(
    db: Session,
    *,
    action: str,
    organisation_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    **details: Any,
) -> None:
    db.add(
        AuditLog(
            organisation_id=organisation_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:300] or None,
            details=_scrub(details),
        )
    )
