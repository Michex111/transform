"""The MCP download path must return a file's real contents, never ciphertext.

WHY this test exists: `download_file` read raw objects out of the bucket and
handed the bytes to an agent. Everything the worker stores — every conversion
output — is at-rest ciphertext beginning with the ``TRENC`` magic, so an agent
asked to read a converted document received **binary noise**.

Nothing in the suite caught it, and the reason is worth keeping in mind:
* the toolbox's fake object store returns whatever it is handed, so it can never
  detect that the real one is undecrypted;
* `upload_file` writes PLAINTEXT (direct uploads are not encrypted at rest), so a
  round-trip test through `upload_file` passes while every conversion output is
  unreadable.

The test below therefore uses the REAL ``FileEncryptionService`` and its real
on-disk format, so an implementation that "decrypts" with a stand-in cannot pass.
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.decrypting_object_reader import (
    DecryptingObjectReader,
)


class _Store:
    """A bucket holding already-stored bytes."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.reads: list[tuple[str, int | None]] = []

    async def read_object(self, object_key: str, max_bytes: int | None = None) -> bytes:
        self.reads.append((object_key, max_bytes))
        data = self.objects[object_key]
        return data if max_bytes is None else data[:max_bytes]

    async def put_object(self, object_key: str, data: bytes) -> None:
        self.objects[object_key] = data


def _service() -> FileEncryptionService:
    return FileEncryptionService(master_key=Fernet.generate_key())


def test_plaintext_object_is_returned_unchanged() -> None:
    """A direct upload is not encrypted, so it must not be treated as ciphertext."""

    async def _run() -> None:
        store = _Store({"k": b"a,b\n1,2\n"})
        reader = DecryptingObjectReader(storage=store, encryption_service=_service())
        assert await reader.read_object("k", user_id="7") == b"a,b\n1,2\n"

    asyncio.run(_run())


def test_encrypted_object_is_decrypted() -> None:
    """The regression: this used to come back as TRENC ciphertext."""

    async def _run() -> None:
        service = _service()
        blob = bytes(range(256)) * 4
        # The worker stores conversion outputs exactly like this.
        stored = service.encrypt_bytes(blob, "7")
        assert stored.startswith(b"TRENC"), "fixture is not the at-rest format"
        store = _Store({"k": stored})

        reader = DecryptingObjectReader(storage=store, encryption_service=service)
        assert await reader.read_object("k", user_id="7") == blob

    asyncio.run(_run())


def test_the_byte_limit_applies_to_the_plaintext_not_the_ciphertext() -> None:
    """The subtlety that makes a naive implementation fail.

    Ciphertext is larger than the plaintext it holds, so reading the object under
    the caller's limit and *then* decrypting truncates the ciphertext and the
    decryption fails — a limit meant to bound the response would instead break
    every download over it.
    """

    async def _run() -> None:
        service = _service()
        blob = b"x" * 5000
        stored = service.encrypt_bytes(blob, "7")
        assert len(stored) > len(blob), "fixture does not demonstrate the problem"
        store = _Store({"k": stored})

        reader = DecryptingObjectReader(storage=store, encryption_service=service)
        out = await reader.read_object("k", 100, user_id="7")
        assert out == blob[:100]

        # And the read was allowed to fetch more than the plaintext limit.
        _, limit_used = store.reads[-1]
        assert limit_used is not None and limit_used > 100

    asyncio.run(_run())


def test_without_an_encryption_service_objects_pass_through() -> None:
    """`ENCRYPTION_MASTER_KEY` unset means nothing at rest is encrypted."""

    async def _run() -> None:
        store = _Store({"k": b"plain"})
        reader = DecryptingObjectReader(storage=store, encryption_service=None)
        assert await reader.read_object("k", user_id="7") == b"plain"

    asyncio.run(_run())


def test_undecryptable_content_raises_rather_than_returning_noise() -> None:
    """Failing loudly beats handing an agent bytes it would describe as a document.

    An agent given ciphertext has no way to know it is not the file, so it would
    report binary junk to the user as their document. The tool turns this into a
    read failure instead.
    """

    async def _run() -> None:
        service = _service()
        # TRENC magic, but encrypted for a different user.
        stored = service.encrypt_bytes(b"secret", "999")
        store = _Store({"k": stored})

        reader = DecryptingObjectReader(storage=store, encryption_service=service)
        with pytest.raises(Exception):
            await reader.read_object("k", user_id="7")

    asyncio.run(_run())


def test_writes_are_stored_as_is() -> None:
    """Uploads are plaintext by design, so the wrapper must not transform them."""

    async def _run() -> None:
        store = _Store({})
        reader = DecryptingObjectReader(storage=store, encryption_service=_service())
        await reader.put_object("k", b"uploaded")
        assert store.objects["k"] == b"uploaded"

    asyncio.run(_run())


def test_a_paged_read_does_not_decrypt_the_whole_object() -> None:
    """Paging must be cheap: only the chunks covering the requested prefix.

    The MCP download tool reads 128 KiB per page. Decrypting the whole object for
    each page (the previous behaviour, forced by the fact that a truncated
    ciphertext cannot authenticate) made one small read cost a full transfer —
    and the storage read was bounded by the same 25 MiB.
    """

    async def _run() -> None:
        service = _service()
        # 2.5 chunks at the default 1 MiB chunk size.
        blob = bytes(i % 251 for i in range(2_500_000))
        stored = service.encrypt_bytes(blob, "7")
        store = _Store({"k": stored})
        reader = DecryptingObjectReader(storage=store, encryption_service=service)

        out = await reader.read_object("k", 128 * 1024, user_id="7")

        assert out == blob[: 128 * 1024]
        _, limit_used = store.reads[-1]
        assert limit_used is not None
        assert limit_used < len(stored), "the whole object was fetched for one page"

    asyncio.run(_run())


@pytest.mark.parametrize(
    "prefix_len",
    [0, 1, 1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1, 2_500_000],
)
def test_a_prefix_at_every_chunk_boundary_matches_a_full_decrypt(prefix_len: int) -> None:
    """Exact chunk boundaries are where a naive prefix cut breaks authentication."""

    async def _run() -> None:
        service = _service()
        blob = bytes(i % 251 for i in range(2_500_000))
        stored = service.encrypt_bytes(blob, "7")
        store = _Store({"k": stored})
        reader = DecryptingObjectReader(storage=store, encryption_service=service)

        out = await reader.read_object("k", prefix_len, user_id="7")
        assert out == blob[:prefix_len]

    asyncio.run(_run())


def test_a_tampered_needed_chunk_still_raises() -> None:
    """Bounding the read must not weaken integrity on the bytes it returns."""

    async def _run() -> None:
        service = _service()
        stored = bytearray(service.encrypt_bytes(b"a" * 3000, "7"))
        # Corrupt a byte inside the first ciphertext chunk (past the header).
        stored[100] ^= 0xFF
        store = _Store({"k": bytes(stored)})
        reader = DecryptingObjectReader(storage=store, encryption_service=service)
        with pytest.raises(Exception):
            await reader.read_object("k", 100, user_id="7")

    asyncio.run(_run())
