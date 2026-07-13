from src.application.ports.session_cache_port import SessionCache

from redis.asyncio import Redis
from datetime import timedelta
from typing import Optional

class RedisSessionAdapter(SessionCache):
    """
    Adapter for Redis session cache operations.
    """

    def __init__(self, redis_client: Redis, prefix: str = "upload_session:"):
        self._client = redis_client
        self._prefix = prefix

    def format_key(self, key: str) -> str:
        """
        Format the key with a prefix to avoid collisions in Redis.

        Args:
            key: The original key.

        Returns:
            The formatted key with the prefix.
        """
        return f"{self._prefix}{key}"

    async def set(self, key: str, data: str, ttl: timedelta) -> None:
        await self._client.setex(self.format_key(key), ttl, data)

    async def get(self, key: str) -> Optional[str]:
        result = await self._client.get(self.format_key(key))
        if not result:
            return None
        return result.decode("utf-8") if isinstance(result, bytes) else result #type: ignore

    async def delete(self, key: str) -> None:
        await self._client.delete(self.format_key(key))
