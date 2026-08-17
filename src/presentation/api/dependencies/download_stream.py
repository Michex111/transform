"""Streaming download helpers for encrypted objects."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter

logger = logging.getLogger(__name__)


async def iter_decrypted_object(
    storage: MinioFileStorageAdapter,
    key: str,
    encryption_service: FileEncryptionService,
    user_id: str,
) -> AsyncIterator[bytes]:
    """
    Stream a stored ciphertext object while decrypting it chunk by chunk.

    The object is fetched from object storage and decrypted incrementally, so
    memory usage stays bounded regardless of file size. The Minio response
    handle is always closed.

    Args:
        storage: File storage adapter that can open object streams.
        key: Object key in the bucket.
        encryption_service: Encryption service holding the master key.
        user_id: Owner id used to derive the per-user decryption key.
    """
    response: Any = await asyncio.to_thread(storage.get_object_stream, key)
    try:
        def _read(n: int) -> bytes:
            chunk = response.read(n)
            return chunk if chunk is not None else b""

        # Drive the synchronous decrypt generator one chunk at a time inside a
        # worker thread so AES-GCM decryption never blocks the event loop.
        iterator = encryption_service.iter_decrypt(_read, user_id)
        while True:
            chunk = await asyncio.to_thread(next, iterator, None)
            if chunk is None:
                break
            yield chunk
    finally:
        try:
            await asyncio.to_thread(response.close)
        except Exception as e:  # pragma: no cover - best-effort close
            logger.warning("Failed to close object stream for %s: %s", key, e)
