from typing import Protocol


class MinioObjectGateway(Protocol):
    """Generates secure object URLs."""

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        """Returns a temporary download URL for an object key."""
        ...
