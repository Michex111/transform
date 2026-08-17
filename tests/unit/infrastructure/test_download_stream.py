"""Tests for the encrypted streaming download helper."""

import asyncio
import io

import pytest

from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.presentation.api.dependencies.download_stream import iter_decrypted_object


class FakeStreamResponse:
    """Mimics the minio get_object response object (read/close)."""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self.closed = True


class FakeStreamingStorage(MinioFileStorageAdapter):
    """MinioFileStorageAdapter stand-in that returns an in-memory stream."""

    def __init__(self, payload: bytes):
        super().__init__(bucket_name="test", s3_client=None)  # type: ignore[arg-type]
        self._payload = payload
        self.last_response: FakeStreamResponse | None = None

    def get_object_stream(self, key: str):
        self.last_response = FakeStreamResponse(self._payload)
        return self.last_response


def test_iter_decrypted_object_streams_plaintext() -> None:
    service = FileEncryptionService()
    ciphertext = service.encrypt_bytes(b"secret document" * 1000, "7")
    storage = FakeStreamingStorage(ciphertext)

    async def _run() -> bytes:
        chunks = []
        async for chunk in iter_decrypted_object(storage, "input.enc", service, "7"):
            chunks.append(chunk)
        return b"".join(chunks)

    plaintext = asyncio.run(_run())
    assert plaintext == b"secret document" * 1000
    assert storage.last_response is not None
    assert storage.last_response.closed


def test_iter_decrypted_object_closes_response_on_error() -> None:
    service = FileEncryptionService()
    # Not a valid Transform-encrypted header → decrypt fails mid-stream
    storage = FakeStreamingStorage(b"garbage that is not encrypted")

    async def _run() -> None:
        async for _ in iter_decrypted_object(storage, "x.enc", service, "7"):
            pass

    with pytest.raises(Exception):
        asyncio.run(_run())

    assert storage.last_response is not None
    assert storage.last_response.closed


def test_iter_decrypted_object_wrong_user_fails() -> None:
    service = FileEncryptionService()
    ciphertext = service.encrypt_bytes(b"for another user", "7")
    storage = FakeStreamingStorage(ciphertext)

    async def _run() -> None:
        async for _ in iter_decrypted_object(storage, "x.enc", service, "8"):
            pass

    with pytest.raises(Exception):
        asyncio.run(_run())

    assert storage.last_response is not None
    assert storage.last_response.closed
