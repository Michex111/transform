from redis.asyncio import Redis


class RedisRateLimiterAdapter:
    """Redis-backed fixed-window rate limiter."""

    def __init__(self, redis_client: Redis, namespace: str = "rate_limit") -> None:
        self._redis_client = redis_client
        self._namespace = namespace

    def _format_key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Increments usage counter and determines if access should be allowed."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero.")

        redis_key = self._format_key(key)
        async with self._redis_client.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds, nx=True)
            results = await pipe.execute()
        count = int(results[0])
        return count <= limit
