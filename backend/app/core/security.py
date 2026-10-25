"""Password hashing (Argon2id), access tokens (JWT) and opaque refresh tokens."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)


class Role(StrEnum):
    ADMIN = "admin"
    RESEARCH_MANAGER = "research_manager"
    RESEARCHER = "researcher"
    VIEWER = "viewer"


class Permission(StrEnum):
    MANAGE_USERS = "manage_users"
    MANAGE_CONNECTIONS = "manage_connections"
    VIEW_OWN_ACCOUNT_DATA = "view_own_account_data"
    MANAGE_PROJECTS = "manage_projects"
    RUN_JOBS = "run_jobs"
    EXPORT = "export"
    EXPORT_OWN_ACCOUNT_DATA = "export_own_account_data"
    EDIT_COSTS = "edit_costs"
    REVIEW_MATCHES = "review_matches"
    VIEW = "view"
    VIEW_AUDIT = "view_audit"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.RESEARCH_MANAGER: frozenset(
        {
            Permission.MANAGE_PROJECTS,
            Permission.RUN_JOBS,
            Permission.EXPORT,
            Permission.EDIT_COSTS,
            Permission.REVIEW_MATCHES,
            Permission.VIEW,
            Permission.VIEW_OWN_ACCOUNT_DATA,
        }
    ),
    Role.RESEARCHER: frozenset(
        {Permission.RUN_JOBS, Permission.EXPORT, Permission.EDIT_COSTS, Permission.VIEW, Permission.MANAGE_PROJECTS}
    ),
    Role.VIEWER: frozenset({Permission.VIEW}),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(*, user_id: uuid.UUID, organisation_id: uuid.UUID, role: str) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = settings.access_token_minutes * 60
    payload = {
        "sub": str(user_id),
        "org": str(organisation_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "iss": settings.jwt_issuer,
        "jti": secrets.token_hex(8),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    return token, ttl


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "org", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid or expired access token") from exc
    if payload.get("typ") != "access":
        raise Unauthorized("Invalid token type")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext token for the client, sha256 digest stored in SQL)."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
