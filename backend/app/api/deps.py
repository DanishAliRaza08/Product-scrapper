"""Request dependencies: authentication, permissions, rate limiting."""

from __future__ import annotations

import ipaddress
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import Forbidden, TooManyRequests, Unauthorized
from app.core.security import Permission, decode_access_token, has_permission
from app.models import User

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


@dataclass
class RequestContext:
    ip: str | None
    user_agent: str | None


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def request_context(request: Request) -> RequestContext:
    # Behind a proxy, configure uvicorn --forwarded-allow-ips so request.client is the real client.
    return RequestContext(
        ip=_valid_ip(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Authentication required")
    payload = decode_access_token(credentials.credentials)
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active or str(user.organisation_id) != payload["org"]:
        raise Unauthorized("User is inactive or no longer exists")
    return user


def require(permission: Permission) -> Callable[[User], User]:
    def checker(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise Forbidden(f"Your role ({user.role}) does not allow: {permission.value}")
        return user

    return checker


class SlidingWindowLimiter:
    """Per-key request limiter. Uses Redis when reachable so limits hold across API
    replicas; falls back to in-process memory otherwise."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._memory: dict[str, deque[float]] = defaultdict(deque)
        self._redis = None
        self._redis_checked = False

    def _client(self):
        if not self._redis_checked:
            self._redis_checked = True
            try:
                import redis

                client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.2, socket_connect_timeout=0.2)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None
        return self._redis

    def hit(self, key: str) -> None:
        client = self._client()
        if client is not None:
            try:
                bucket = f"rl:{key}:{int(time.time() // self.window)}"
                count = client.incr(bucket)
                if count == 1:
                    client.expire(bucket, self.window)
                if count > self.limit:
                    raise TooManyRequests("Too many attempts; try again shortly")
                return
            except TooManyRequests:
                raise
            except Exception:  # Redis unavailable mid-flight: fall back to the in-memory window
                log.debug("rate limiter falling back to memory", exc_info=True)
        now = time.monotonic()
        q = self._memory[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise TooManyRequests("Too many attempts; try again shortly")
        q.append(now)

    def reset(self) -> None:
        self._memory.clear()


login_limiter = SlidingWindowLimiter(get_settings().login_rate_limit_per_minute, 60)
