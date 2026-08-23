"""Rate limiting middleware for FastAPI.

Uses a Redis-backed sliding-window rate limiter when Redis is available,
and falls back to an in-memory sliding window otherwise.
"""

import hashlib
import logging
import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.adapters.security.rate_limiter import RedisRateLimiter
from src.infrastructure.config.settings import get_settings
from src.infrastructure.redis.client import create_redis_client

logger = logging.getLogger(__name__)

_AUTH_PATHS = {"/api/users/token", "/api/users/register", "/api/users/refresh"}

# Every constructed middleware instance. Tests use this to reset the in-memory
# fallback state so per-IP counters do not accumulate across test cases.
_INSTANCES: list["RateLimitMiddleware"] = []


def _flush_redis_rate_limits() -> None:
    """Best-effort flush of Redis rate-limit keys.

    Used by ``reset_all`` so tests running against a reachable local Redis
    start from a fresh budget. Falls back to a no-op when Redis is unreachable
    or the sync client is unavailable.
    """
    try:
        from redis import Redis as SyncRedis

        settings = get_settings()
        redis = SyncRedis.from_url(settings.REDIS_URL.get_secret_value(), socket_timeout=3)
        for key in redis.scan_iter(match="ratelimit:*", count=500):
            redis.delete(key)
        redis.close()
    except Exception:  # pragma: no cover - Redis may not be present in tests
        pass


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiting middleware.

    - Authenticated requests are keyed per API key (when ``X-API-Key`` is
      present) so per-key limits are enforced cluster-wide.
    - Everything else is keyed per client IP.
    - Auth endpoints (login/register/refresh) get a stricter limit to slow
      brute-force attempts.
    - When Redis is reachable limits are cluster-wide; otherwise an in-memory
      fallback keeps the service functional (per-process only).
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client
        self._redis_limiter = (
            RedisRateLimiter(redis_client) if redis_client is not None else None
        )
        self._store: dict[str, list[float]] = {}  # In-memory fallback
        self._redis_unavailable = False
        self._settings = get_settings()
        _INSTANCES.append(self)

    @classmethod
    def reset_all(cls) -> None:
        """Clear all in-memory rate-limit state (used by tests).

        Also clears Redis-backed rate-limit keys so tests that run against a
        reachable local Redis start from a fresh budget (otherwise counters
        accumulate across test runs and cause spurious 429s).
        """
        for instance in _INSTANCES:
            instance._store.clear()
            instance._redis_unavailable = False
        _flush_redis_rate_limits()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only rate-limit API routes. The SPA (index.html, /assets/*) is served
        # by this app and must not consume the client's API budget.
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        key, limit = self._resolve_limit(request)

        allowed = await self._is_allowed(key, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        return await call_next(request)

    def _resolve_limit(self, request: Request) -> tuple[str, int]:
        """Determine the rate-limit key and window limit for a request."""
        # Per-API-key limits take priority over everything else.
        api_key = request.headers.get("x-api-key")
        if api_key:
            key = "apikey:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            return key, self._settings.RATE_LIMIT_API_KEY_DEFAULT

        client_ip = request.client.host if request.client else "unknown"

        # Authenticated users are keyed by their access token (stable for the
        # token lifetime) and get a much higher limit than anonymous IPs, so
        # legitimate SPA usage is not throttled by the shared-IP free limit.
        authorization = request.headers.get("authorization")
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:]
            key = "user:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
            return key, self._settings.RATE_LIMIT_AUTHENTICATED

        # Stricter limit for credential-guessing endpoints (anonymous only).
        if request.url.path in _AUTH_PATHS:
            return f"ip:{client_ip}:auth", self._settings.RATE_LIMIT_AUTH

        # Guest endpoints get the guest limit.
        if "/guest" in request.url.path:
            return f"ip:{client_ip}", self._settings.RATE_LIMIT_GUEST

        return f"ip:{client_ip}", self._settings.RATE_LIMIT_FREE

    async def _is_allowed(self, key: str, limit: int, window: int = 60) -> bool:
        """Check if request is allowed under the rate limit."""
        if self._redis_limiter is not None and not self._redis_unavailable:
            try:
                return await self._redis_limiter.is_allowed(key, limit, window)
            except Exception as e:  # Redis down — fall back to in-memory
                self._redis_unavailable = True
                # Fail-open but alert loudly: with multiple replicas the
                # per-instance in-memory counters drift, so an attacker can
                # work around the limit by hitting different instances. Signal
                # this as an ERROR (not a warning) for operator visibility.
                logger.error(
                    "Rate limiter DEGRADED to per-instance in-memory fallback "
                    "(cluster-wide limits DISABLED). Redis unreachable: %s",
                    e,
                )

        # In-memory fallback (single-process only)
        now = time.time()
        if key not in self._store:
            self._store[key] = []

        # Remove expired entries
        self._store[key] = [t for t in self._store[key] if now - t < window]

        if len(self._store[key]) >= limit:
            return False

        self._store[key].append(now)
        return True


def build_rate_limit_middleware(app) -> RateLimitMiddleware:
    """
    Create a rate-limit middleware wired to Redis when configured.

    Uses a short connect timeout so an unreachable Redis degrades to the
    in-memory fallback instead of stalling requests.
    """
    try:
        settings = get_settings()
        redis_client = create_redis_client(settings.REDIS_URL.get_secret_value())
    except Exception as e:
        logger.warning("Rate limiter started without Redis: %s", e)
        redis_client = None

    return RateLimitMiddleware(app, redis_client=redis_client)
