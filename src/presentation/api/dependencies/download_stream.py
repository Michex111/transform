"""Streaming download helpers for encrypted objects."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from src.infrastructure.adapters.security.encryption import (
    FileEncryptionService,
    _STREAM_HEADER_LEN,
    _STREAM_MAGIC,
)
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter

logger = logging.getLogger(__name__)


async def iter_decrypted_object(
    storage: MinioFileStorageAdapter,
    key: str,
    encryption_service: FileEncryptionService,
    user_id: str,
) -> AsyncIterator[bytes]:
    """
    Stream a stored object while decrypting it chunk by chunk.

    The object is fetched from object storage and decrypted incrementally, so
    memory usage stays bounded regardless of file size. The Minio response
    handle is always closed.

    Objects that are NOT at-rest ciphertext (e.g. direct browser uploads, which
    are written plaintext to storage) are passed through unchanged — we stop
    trying to "decrypt" them, which previously raised a bad-header error and
    returned an empty body.

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

        # Peek the full ciphertext header to decide whether to decrypt. Direct
        # browser uploads are plaintext and must stream through untouched;
        # previously we always tried to decrypt them, which raised a bad-header
        # error and returned an empty body (0-byte downloads).
        head = _read(_STREAM_HEADER_LEN)
        if head[:len(_STREAM_MAGIC)] != _STREAM_MAGIC:
            # Plaintext object: yield the bytes we already read, then stream the rest.
            if head:
                yield head
            while True:
                chunk = await asyncio.to_thread(_read, 1024 * 1024)
                if not chunk:
                    break
                yield chunk
            return

        # Ciphertext: serve the already-read header bytes to the first
        # read_chunk call so the decrypt iterator gets the full header, then
        # stream the remaining ciphertext from the response.
        deferred = head

        def _peeked_read(n: int) -> bytes:
            nonlocal deferred
            if deferred:
                out, deferred = deferred[:n], deferred[n:]
                return out
            chunk = _read(n)
            return chunk if chunk is not None else b""

        # Drive the synchronous decrypt generator one chunk at a time inside a
        # worker thread so AES-GCM decryption never blocks the event loop.
        iterator = encryption_service.iter_decrypt(_peeked_read, user_id)
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
