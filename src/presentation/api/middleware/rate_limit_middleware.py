from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.infrastructure.adapters.queues.redis_rate_limiter_adapter import RedisRateLimiterAdapter
from src.infrastructure.config.settings import get_settings
from src.infrastructure.redis.client import create_redis_client


class APIRateLimitMiddleware(BaseHTTPMiddleware):
    """Applies Redis-backed rate limits for SDK and guest web routes."""

    def __init__(self, app) -> None:
        super().__init__(app)
        settings = get_settings()
        redis_client = create_redis_client(settings.REDIS_URL.get_secret_value())
        self._rate_limiter = RedisRateLimiterAdapter(redis_client=redis_client)
        self._sdk_limit = settings.SDK_RATE_LIMIT_LIMIT
        self._sdk_window = settings.SDK_RATE_LIMIT_WINDOW_SECONDS
        self._guest_limit = settings.GUEST_RATE_LIMIT_LIMIT
        self._guest_window = settings.GUEST_RATE_LIMIT_WINDOW_SECONDS

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1/sdk/"):
            client_key = request.headers.get("X-API-Key") or (request.client.host if request.client else "unknown")
            allowed = await self._rate_limiter.allow(
                key=f"sdk:{client_key}",
                limit=self._sdk_limit,
                window_seconds=self._sdk_window,
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "SDK rate limit exceeded."},
                )

        if path.startswith("/api/v1/web/conversions/guest/"):
            guest_ip = request.client.host if request.client else "unknown"
            allowed = await self._rate_limiter.allow(
                key=f"guest:web:{guest_ip}",
                limit=self._guest_limit,
                window_seconds=self._guest_window,
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Guest rate limit exceeded."},
                )

        return await call_next(request)
