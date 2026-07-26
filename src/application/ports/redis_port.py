from typing import Protocol

from src.domain.conversions.entities.conversion_job import ConversionJob


class RedisQueuePort(Protocol):
    """Publishes jobs to Redis streams."""

    async def publish_job(self, stream: str, job: ConversionJob) -> str:
        """Pushes a conversion job to a specific stream and returns message id."""
        ...


class RateLimiterPort(Protocol):
    """Provides distributed rate-limiting primitives."""

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Returns True when request is allowed in the current window."""
        ...
