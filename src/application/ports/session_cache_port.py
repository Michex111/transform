from typing import Protocol
from datetime import timedelta

class SessionCache(Protocol):
    """
    Protocol for session cache operations.
    """

    async def set(self, key: str, data: str, ttl: timedelta) -> None:
        """
        Set a value in the session cache with an expiration time.

        Args:
            key: The key under which the value is stored.
            data: The data to be stored.
            ttl: Time after which the key-value pair expires.
        """
        ...

    async def get(self, key: str) -> str | None:
        """
        Retrieve a value from the session cache.

        Args:
            key: The key whose value is to be retrieved.

        Returns:
            The value associated with the key, or None if the key does not exist or has expired.
        """
        ...

    async def delete(self, key: str) -> None:
        """
        Delete a value from the session cache.

        Args:
            key: The key whose value is to be deleted.
        """
        ...