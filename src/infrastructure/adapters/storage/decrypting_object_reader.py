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
#: bounded.
_CIPHERTEXT_SLACK = 64 * 1024
#: Extra bytes to read so a plaintext prefix is backed by the WHOLE ciphertext
#: chunk that contains it.
#:
#: The at-rest format encrypts in chunks (1 MiB by default), and a chunk only
#: authenticates when it is complete. Asking storage for exactly
#: ``plaintext_prefix + tag`` therefore cuts the last chunk in half and the prefix
#: cannot be decrypted at all. One chunk of headroom guarantees the chunk that
#: holds the requested last byte arrives whole, while still bounding a paged read
#: to a page plus 1 MiB rather than the whole object.
_CIPHERTEXT_CHUNK_SLACK = 1024 * 1024


class _ObjectWriter(Protocol):
    async def read_object(self, object_key: str, max_bytes: int | None = None) -> bytes: ...

    async def put_object(self, object_key: str, data: bytes) -> None: ...


class _Decryptor(Protocol):
    def decrypt_bytes(self, ciphertext: bytes, user_id: str) -> bytes: ...

    def decrypt_prefix(
        self,
        ciphertext: bytes,
        user_id: str,
        max_plaintext_bytes: int,
        *,
        complete_object: bool = False,
    ) -> bytes: ...


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

        Only the ciphertext needed for ``max_bytes`` is read and decrypted. The
        MCP download tool pages at 128 KiB, and decrypting the whole object per
        page made one small read cost a full transfer from storage every time.
        """
        # The stored object may be larger than the plaintext we are willing to
        # return, so read against a ciphertext-sized bound. The extra chunk of
        # headroom keeps the last needed chunk whole (see the constant).
        read_limit = (
            None
            if max_bytes is None
            else max_bytes + _CIPHERTEXT_SLACK + _CIPHERTEXT_CHUNK_SLACK
        )
        raw = await self._storage.read_object(object_key, max_bytes=read_limit)

        if self._encryption is None or not raw.startswith(_STREAM_MAGIC):
            # Plaintext (a direct upload, or a deployment without at-rest
            # encryption). Truncate to the caller's limit, which is the behaviour
            # the toolbox's size check is written against.
            return raw if max_bytes is None else raw[:max_bytes]

        try:
            if max_bytes is None:
                return self._encryption.decrypt_bytes(raw, user_id)
            # Decrypt only the chunks that cover the requested prefix; the
            # returned bytes are authenticated exactly as a full decrypt would
            # produce them. ``complete_object`` tells the service whether a short
            # final chunk is the real end of the file or was cut by the read
            # bound above (a fragment cannot authenticate).
            complete_object = read_limit is not None and len(raw) < read_limit
            plaintext = self._encryption.decrypt_prefix(
                raw, user_id, max_bytes, complete_object=complete_object
            )
            if len(plaintext) < max_bytes and not complete_object:
                # The storage read stopped before the chunk we needed (a chunk
                # size larger than the slack above). Failing is safer than
                # silently returning a short prefix the caller would read as EOF.
                raise ValueError(
                    "The encrypted object's prefix could not be read completely."
                )
            return plaintext
        except Exception:  # noqa: BLE001 - a corrupt or foreign-key object
            # Never surface ciphertext as if it were the file: an agent that
            # received it would describe binary noise to the user as their
            # document. Raising lets the tool report a read failure instead.
            logger.warning(
                "Could not decrypt object for download (key=%s)", object_key
            )
            raise
