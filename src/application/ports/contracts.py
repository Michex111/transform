from pathlib import Path
from typing import Optional, Protocol

from src.domain.conversions.entities.conversion_job import ConversionJob

class PersistenceQueuePort(Protocol):
    async def enqueue_save(self, job: ConversionJob) -> None: ...

class FileStorageGateway(Protocol):
    def download(self, key: str, dest_path: Path) -> None: ...

    def upload(self, target_key: str, source_path: Path) -> None: ...

# TODO: clean up required for unrequired ports 

class RateLimiterPort(Protocol):
    """Provides distributed rate-limiting primitives."""

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Returns True when request is allowed in the current window."""
        ...






