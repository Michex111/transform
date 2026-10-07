"""Copy one object to another key inside the same bucket.

The MCP ``save_file`` tool promotes a conversion's output into the user's
Drive. That requires moving bytes between two keys, and the alternative —
handing the application layer a full storage adapter — would also hand it
``remove_object``, presigning and arbitrary-key reads.

This adapter therefore exposes exactly one capability (``copy_object``) and
keeps the mechanics where they belong: a temporary file (the underlying client
is a synchronous streaming API) and a thread hop so the event loop is never
blocked by an S3 round trip.

Both keys are validated with :func:`sanitize_object_key` before use. The source
comes from a job row and the target from our own upload session, so neither is
attacker-controlled today — validating anyway is what keeps that true if a
future caller passes something else.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.infrastructure.adapters.storage.sanitize import sanitize_object_key

logger = logging.getLogger(__name__)


class MinioObjectCopier:
    """Copies an object to a new key within the configured bucket."""

    def __init__(self, *, storage: MinioFileStorageAdapter) -> None:
        self._storage = storage

    async def copy_object(self, source_key: str, target_key: str) -> int:
        """Copy ``source_key`` to ``target_key``; return the copied size in bytes."""
        source = sanitize_object_key(source_key)
        target = sanitize_object_key(target_key)
        if source == target:
            # A no-op copy would truncate nothing but is certainly a caller bug.
            raise ValueError("copy_object requires distinct source and target keys.")
        return await asyncio.to_thread(self._copy_blocking, source, target)

    def _copy_blocking(self, source_key: str, target_key: str) -> int:
        with tempfile.TemporaryDirectory(prefix="mcp-copy-") as scratch:
            payload = Path(scratch) / "payload"
            self._storage.download(source_key, payload)
            size = payload.stat().st_size
            self._storage.upload(target_key, payload)
            logger.info(
                "MCP save_file copied %s bytes from %s to %s", size, source_key, target_key
            )
            return size
