"""Resilient HTTP for connectors: rate limiting, retries with backoff, Retry-After,
and a circuit breaker. Credentials and query strings are never logged."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import (
    AuthenticationFailed,
    ConnectorError,
    PermissionDenied,
    ProductUnavailable,
    RateLimited,
    RateLimitPolicy,
    TransientError,
)
from app.core.config import get_settings

log = logging.getLogger(__name__)


class TokenBucket:
    """Async token bucket. One bucket per connector per process; combine with the
    worker concurrency setting to bound the global request rate."""

    def __init__(self, rate: float, burst: int, clock=time.monotonic):
        self.rate = rate
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.clock = clock
        self.updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_after: float = 60.0, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_after = reset_after
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.reset_after:
            return True  # half-open: let one request probe the source
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = self.clock()


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Full-jitter exponential backoff."""
    return random.uniform(0, min(cap, base * (2**attempt)))


class ResilientClient:
    def __init__(
        self,
        name: str,
        policy: RateLimitPolicy,
        *,
        base_url: str = "",
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ):
        self.name = name
        self.bucket = TokenBucket(policy.requests_per_second, policy.burst)
        self.semaphore = asyncio.Semaphore(policy.max_concurrency)
        self.breaker = CircuitBreaker()
        self.max_retries = max_retries
        self._sleep = sleep
        self._base_url = base_url
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        # Created lazily so that building a connector (e.g. to read its manifest)
        # does not open network resources.
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=get_settings().http_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                headers={"User-Agent": "MPI-ProductIntelligence/0.1 (+authorised-api-client)"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: ConnectorError | None = None
        for attempt in range(self.max_retries + 1):
            if not self.breaker.allow():
                raise TransientError(f"{self.name}: circuit open after repeated failures")
            await self.bucket.acquire()
            try:
                async with self.semaphore:
                    response = await self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.breaker.record_failure()
                last_error = TransientError(f"{self.name}: network error ({type(exc).__name__})")
                await self._sleep(backoff_delay(attempt))
                continue

            status = response.status_code
            if status < 400:
                self.breaker.record_success()
                return response
            if status == 429:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                last_error = RateLimited(f"{self.name}: rate limited", retry_after=retry_after)
                await self._sleep(retry_after if retry_after is not None else backoff_delay(attempt, base=1.0))
                continue
            if status >= 500:
                self.breaker.record_failure()
                last_error = TransientError(f"{self.name}: source error HTTP {status}")
                await self._sleep(backoff_delay(attempt))
                continue
            # 4xx other than 429 are not retried.
            self.breaker.record_success()
            if status == 401:
                raise AuthenticationFailed(f"{self.name}: authentication failed")
            if status == 403:
                raise PermissionDenied(f"{self.name}: access denied for this resource or scope")
            if status in (404, 410):
                raise ProductUnavailable(f"{self.name}: resource not found or no longer available")
            raise ConnectorError(f"{self.name}: request rejected (HTTP {status})")
        assert last_error is not None
        log.warning("connector request exhausted retries", extra={"connector": self.name, "error": last_error.code})
        raise last_error

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return (await self.request("GET", url, **kwargs)).json()

    async def post_form(self, url: str, data: dict[str, str], **kwargs: Any) -> Any:
        return (await self.request("POST", url, data=data, **kwargs)).json()
