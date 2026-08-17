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

from src.infrastructure.adapters.security.rate_limiter import (
    RedisRateLimiter,
    get_tier_rate_limit,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.redis.client import create_redis_client

logger = logging.getLogger(__name__)

_AUTH_PATHS = {"/api/users/token", "/api/users/register", "/api/users/refresh"}


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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health/ready/metrics/docs endpoints
        if request.url.path in ("/health", "/ready", "/metrics", "/docs", "/openapi.json"):
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
        # Per-API-key limits take priority over IP limits.
        api_key = request.headers.get("x-api-key")
        if api_key:
            key = "apikey:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            return key, self._settings.RATE_LIMIT_API_KEY_DEFAULT

        client_ip = request.client.host if request.client else "unknown"

        # Stricter limit for credential-guessing endpoints.
        if request.url.path in _AUTH_PATHS:
            return f"ip:{client_ip}:auth", self._settings.RATE_LIMIT_AUTH

        # Guest endpoints get the guest limit.
        if "/guest" in request.url.path:
            return f"ip:{client_ip}", get_tier_rate_limit("guest")

        return f"ip:{client_ip}", get_tier_rate_limit("free")

    async def _is_allowed(self, key: str, limit: int, window: int = 60) -> bool:
        """Check if request is allowed under the rate limit."""
        if self._redis_limiter is not None and not self._redis_unavailable:
            try:
                return await self._redis_limiter.is_allowed(key, limit, window)
            except Exception as e:  # Redis down — fall back to in-memory
                self._redis_unavailable = True
                logger.warning("Redis rate limiter unavailable, using in-memory fallback: %s", e)

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
