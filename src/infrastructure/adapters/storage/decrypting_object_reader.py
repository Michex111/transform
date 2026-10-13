"""A byte-level object reader that returns a file's real contents.

WHY this exists: the MCP ``download_file`` tool read raw objects out of the
bucket and handed the bytes straight to an agent. Anything the worker had stored
— every conversion output — is at-rest ciphertext beginning with the ``TRENC``
magic, so an agent asked to read a converted document received **ciphertext**.

That was invisible to the test suite because the toolbox's fake object store
returns whatever it was given, and because ``upload_file`` writes PLAINTEXT
(direct uploads are not encrypted at rest). So a round-trip test through
``upload_file`` passed while every real conversion output was unreadable.

The rule here mirrors ``presentation/api/dependencies/download_stream.py``, which
is what the REST download path uses and which already solved this:

* no ``TRENC`` magic  → the object is plaintext (a direct upload); pass it through
* ``TRENC`` magic     → decrypt it with the owner's key

Keeping the decision in one place and in the infrastructure layer means the MCP
surface never learns about the encryption format, and cannot drift from the
REST surface's rule.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: The at-rest ciphertext magic. Duplicated from
#: ``infrastructure/adapters/security/encryption.py`` (``_STREAM_MAGIC``) rather
#: than imported: reading it from a private name there would couple this module
#: to that one's internals, and the value is a frozen on-disk format marker.
_STREAM_MAGIC = b"TRENC"

#: How much larger than the plaintext limit the ciphertext may be. The format is
#: a small fixed header plus a per-chunk authentication tag, so the overhead is a
#: fraction of a percent; this is a generous bound that still keeps the read
#: bounded. Reading the ciphertext under the PLAINTEXT limit would truncate it and
#: make decryption fail — the reason the limit is applied after decrypting.
_CIPHERTEXT_SLACK = 64 * 1024


class _ObjectWriter(Protocol):
    async def read_object(self, object_key: str, max_bytes: int | None = None) -> bytes: ...

    async def put_object(self, object_key: str, data: bytes) -> None: ...


class _Decryptor(Protocol):
    def decrypt_bytes(self, ciphertext: bytes, user_id: str) -> bytes: ...


class DecryptingObjectReader:
    """Byte-level object access whose reads return the file's real contents.

    Satisfies the MCP ``MCPObjectStorePort``. Named for its one distinguishing
    behaviour: ``read_object`` decrypts. The write half is a straight pass-through
    and deliberately so — an uploaded file is written to storage as plaintext (the
    same choice the upload path makes everywhere), so there is nothing to invert
    on the way in. Encrypting here would create a second, divergent at-rest format.

    ``put_object`` is included rather than split into a second collaborator
    because the tool needs one thing: byte access to a single object it has
    already resolved and ownership-checked.
    """

    def __init__(
        self,
        *,
        storage: _ObjectWriter,
        encryption_service: _Decryptor | None = None,
    ) -> None:
        self._storage = storage
        #: ``None`` when ``ENCRYPTION_MASTER_KEY`` is unset, in which case
        #: everything at rest is plaintext and there is nothing to decrypt.
        self._encryption = encryption_service

    async def put_object(self, object_key: str, data: bytes) -> None:
        """Store bytes as-is. Uploads are plaintext by design; see the class note."""
        await self._storage.put_object(object_key, data)

    async def read_object(
        self,
        object_key: str,
        max_bytes: int | None = None,
        *,
        user_id: str,
    ) -> bytes:
        """Return the object's plaintext, at most ``max_bytes`` of it.

        ``user_id`` is required because the at-rest key is derived per user: the
        content of a file cannot be read without knowing whose file it is. That
        is also why the caller must have established ownership before getting
        here.
        """
        # The stored object may be larger than the plaintext we are willing to
        # return, so read against a ciphertext-sized bound.
        read_limit = None if max_bytes is None else max_bytes + _CIPHERTEXT_SLACK
        raw = await self._storage.read_object(object_key, max_bytes=read_limit)

        if self._encryption is None or not raw.startswith(_STREAM_MAGIC):
            # Plaintext (a direct upload, or a deployment without at-rest
            # encryption). Truncate to the caller's limit, which is the behaviour
            # the toolbox's size check is written against.
            return raw if max_bytes is None else raw[:max_bytes]

        try:
            plaintext = self._encryption.decrypt_bytes(raw, user_id)
        except Exception:  # noqa: BLE001 - a corrupt or foreign-key object
            # Never surface ciphertext as if it were the file: an agent that
            # received it would describe binary noise to the user as their
            # document. Raising lets the tool report a read failure instead.
            logger.warning(
                "Could not decrypt object for download (key=%s)", object_key
            )
            raise
        return plaintext if max_bytes is None else plaintext[:max_bytes]
