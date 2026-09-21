"""
Redis-based sliding window rate limiter.

Supports per-tier and per-API-key rate limiting with configurable
windows and limits.
"""

import time
from typing import Protocol


class RateLimiterPort(Protocol):
    """Protocol for rate limiting implementations."""

    async def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool: ...

    async def get_remaining(self, key: str, limit: int, window_seconds: int = 60) -> int: ...


class RedisRateLimiter:
    """Redis-based sliding window rate limiter."""

    def __init__(self, redis_client):
        self._redis = redis_client

    async def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """
        Check if a request is allowed under the rate limit.

        Args:
            key: Unique identifier (IP, user_id, api_key).
            limit: Maximum requests allowed in the window.
            window_seconds: Size of the sliding window in seconds.

        Returns:
            True if the request is allowed, False otherwise.
        """
        remaining = await self.get_remaining(key, limit, window_seconds)
        return remaining > 0

    async def get_remaining(self, key: str, limit: int, window_seconds: int = 60) -> int:
        """
        Get the number of remaining requests for a key.

        Uses a sorted-set sliding window algorithm.
        """
        now_ms = int(time.time() * 1000)
        window_start = now_ms - (window_seconds * 1000)
        redis_key = f"ratelimit:{key}"

        async with self._redis.pipeline(transaction=True) as pipe:
            # Remove expired entries
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # Add the current request
            pipe.zadd(redis_key, {str(now_ms): now_ms})
            # Count entries (now includes this request)
            pipe.zcard(redis_key)
            # Set expiry on the key
            pipe.expire(redis_key, window_seconds + 1)
            results = await pipe.execute()

        current_count = results[2]  # zcard result (after adding this request)
        return max(0, limit - current_count)
