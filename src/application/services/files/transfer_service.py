import logging

from src.application.ports.object_storage_port import StorageUrlGateway
from src.application.ports.session_cache_port import SessionCache
from src.application.exceptions.file_transfer_exceptions import (
    InvalidPartNumberError,
    MissingUploadPartsError,
    UploadNotMultipartError,
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.infrastructure.adapters.storage.sanitize import (
    extension_from_filename,
    normalize_extension,
    sanitize_filename,
    sanitize_object_key,
)
from uuid import uuid4
from datetime import timedelta


def plan_multipart(file_size: int, *, part_size: int) -> tuple[int, int]:
    """Return ``(part_count, part_size)`` for a multipart upload of ``file_size``.

    Pure arithmetic, kept module-level so it is testable without a gateway.

    Only the FINAL part may be smaller than ``part_size`` (S3 allows only the
    last part to fall under the 5 MiB minimum), so a size that is an exact
    multiple of the part size yields exactly ``file_size / part_size`` parts and
    no extra empty one.
    """
    if file_size <= 0:
        raise ValueError("file_size must be positive for a multipart upload.")
    if part_size <= 0:
        raise ValueError("part_size must be positive.")
    part_count = -(-file_size // part_size)  # ceil division
    return part_count, part_size


class TransferService:
    def __init__(
        self,
        storage_port: StorageUrlGateway,
        cache_port: SessionCache,
        ttl_minutes: int = 10,
        logger: logging.Logger = logging.getLogger(__name__),
        *,
        large_ttl_minutes: int | None = None,
        multipart_threshold_bytes: int | None = None,
        multipart_part_size_bytes: int | None = None,
    ):
        self._storage = storage_port
        self._cache = cache_port
        self._ttl = timedelta(minutes=ttl_minutes)
        self._logger = logger
        # Resolved lazily from settings when not injected, so a service built by
        # a test with only the historical arguments still works and so the
        # defaults live in exactly one place (settings.py). Only ``file_size``
        # -aware callers ever touch these, which keeps the old single-PUT path
        # free of any new configuration dependency.
        self._large_ttl_minutes = large_ttl_minutes
        self._multipart_threshold_bytes = multipart_threshold_bytes
        self._multipart_part_size_bytes = multipart_part_size_bytes

    # ------------------------------------------------------------------
    # Configuration resolution
    # ------------------------------------------------------------------

    @property
    def large_ttl_minutes(self) -> int:
        """TTL used for multipart sessions and for part URLs."""
        if self._large_ttl_minutes is None:
            from src.infrastructure.config.settings import get_settings

            self._large_ttl_minutes = get_settings().LARGE_UPLOAD_URL_TTL_MINUTES
        return self._large_ttl_minutes

    @property
    def multipart_threshold_bytes(self) -> int:
        """Declared size at/above which the session switches to multipart."""
        if self._multipart_threshold_bytes is None:
            from src.infrastructure.config.settings import get_settings

            self._multipart_threshold_bytes = get_settings().MULTIPART_THRESHOLD_BYTES
        return self._multipart_threshold_bytes

    @property
    def part_size_bytes(self) -> int:
        if self._multipart_part_size_bytes is None:
            from src.infrastructure.config.settings import get_settings

            self._multipart_part_size_bytes = get_settings().MULTIPART_PART_SIZE_BYTES
        return self._multipart_part_size_bytes

    def _session_ttl(self, session: UploadSession) -> timedelta:
        """The TTL a session (and its URLs) were minted with.

        A multipart session must outlive a multi-gigabyte transfer, so it is
        refreshed with the long window on every write; a single-PUT session
        keeps the short one.
        """
        if session.upload_mode == "multipart":
            return timedelta(minutes=self.large_ttl_minutes)
        return self._ttl

    async def create_upload(
        self,
        file_extension: str,
        user_id: str,
        file_name: str | None = None,
        folder_id: str | None = None,
        file_size: int | None = None,
        max_file_size_bytes: int | None = None,
    ) -> UploadResponse:
        """Create an upload session.

        ``file_size`` (bytes, optional) is the client's DECLARED size. When it
        is supplied and at/above ``multipart_threshold_bytes`` the session is
        created as a multipart upload; when it is omitted (the guest flow and
        older clients) the behaviour is exactly the historical single presigned
        PUT, with no pre-check, so nothing that works today changes.

        The multipart upload is created EAGERLY here rather than on the first
        part URL. Tradeoff: a provider-side failure surfaces immediately instead
        of after a multi-gigabyte transfer, at the cost of leaving an
        incomplete-multipart object in the bucket if the client abandons the
        session. That abandonment is already covered — bucket lifecycle rules
        (B2/S3) reap incomplete multipart uploads — and an explicit
        ``DELETE /api/uploads/sessions/{id}`` aborts it right away.
        """
        upload_id = str(uuid4())
        object_key = self._generate_object_key("upload/" + upload_id, file_extension)

        # Sanitize the user-supplied file name to a safe leaf segment.
        safe_name = sanitize_filename(file_name) if file_name else None

        multipart = file_size is not None and file_size >= self.multipart_threshold_bytes

        session = UploadSession(
            upload_id=upload_id,
            object_key=object_key,
            status="pending",
            file_name=safe_name,
            file_extension=normalize_extension(file_extension)
            or extension_from_filename(safe_name),
            folder_id=folder_id,
            user_id=user_id,
        )

        upload_url: str | None = None
        part_size: int | None = None
        part_count: int | None = None

        if multipart:
            assert file_size is not None  # implied by ``multipart``
            part_count, part_size = plan_multipart(file_size, part_size=self.part_size_bytes)
            # Eager creation: the provider upload id is persisted with the
            # session so a later /parts or /verify call can address it.
            session.multipart_upload_id = self._storage.create_multipart_upload(object_key)
            session.upload_mode = "multipart"
            session.part_size_bytes = part_size
            session.part_count = part_count
        else:
            upload_url = self._storage.generate_put_url(object_key)
        session.declared_size = file_size

        ttl = self._session_ttl(session)
        await self._cache.set(upload_id, session.model_dump_json(), ttl=ttl)

        self._logger.info(
            "Created %s upload session %s for user %s",
            session.upload_mode,
            upload_id,
            user_id,
        )

        return UploadResponse(
            upload_id=upload_id,
            upload_url=upload_url,
            object_key=object_key,
            expires_in_minutes=int(ttl.total_seconds() // 60),
            upload_mode=session.upload_mode,
            part_size_bytes=part_size,
            part_count=part_count,
            max_file_size_bytes=max_file_size_bytes,
        )

    async def get_upload_session(self, upload_id: str) -> UploadSession:
        session_data = await self._cache.get(upload_id)
        if not session_data:
            self._logger.warning(f"Upload session not found for upload_id: {upload_id}")
            raise UploadSessionNotFoundError(f"Session {upload_id} invalid or expired")

        return UploadSession.model_validate_json(session_data)

    async def delete_upload_session(self, upload_id: str):
        """Delete a session, aborting any multipart upload it still owns.

        The abort is what stops an abandoned large upload from keeping its
        already-uploaded parts (and the storage they occupy) around until a
        lifecycle rule happens to collect them. Its failure is logged rather
        than raised: the client asked for the session to go away, and a provider
        hiccup should not turn a cleanup call into an error the caller can do
        nothing about.
        """
        session = await self._load_session_or_none(upload_id)
        if session is not None and session.multipart_upload_id is not None:
            try:
                await self._storage.abort_multipart_upload(
                    session.object_key, session.multipart_upload_id,
                )
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                self._logger.warning(
                    "Failed to abort multipart upload for session %s: %s", upload_id, exc,
                )
        await self._cache.delete(upload_id)
        self._logger.info(f"Upload session deleted for upload_id: {upload_id}")

    async def generate_part_urls(
        self, session: UploadSession, part_numbers: list[int],
    ) -> list[tuple[int, str]]:
        """Presign one PUT URL per requested part, ascending.

        Takes an already-loaded ``session`` rather than an id so the caller's
        ownership check and this call cannot straddle two reads of the cache
        (the session cannot change underneath the authorization decision).
        """
        if session.upload_mode != "multipart" or session.multipart_upload_id is None:
            raise UploadNotMultipartError(
                "This upload session does not use multipart upload; PUT the file "
                "to the session's upload_url instead."
            )

        part_count = session.part_count or 0
        # Reject out-of-range numbers instead of minting a URL the provider
        # would fail on. Sorted and deduplicated so the client gets one URL per
        # part in a stable order regardless of the order it asked in.
        requested = sorted(set(part_numbers))
        for number in requested:
            if number < 1 or number > part_count:
                raise InvalidPartNumberError(
                    f"Part {number} is outside the valid range 1..{part_count}."
                )

        assert session.multipart_upload_id is not None
        return [
            (
                number,
                self._storage.generate_part_upload_url(
                    session.object_key,
                    number,
                    session.multipart_upload_id,
                    self.large_ttl_minutes,
                ),
            )
            for number in requested
        ]

    async def verify_upload_completion(
        self, upload_id: str, parts: list[tuple[int, str]] | None = None,
    ) -> UploadSession:
        """Finalize an upload.

        For a multipart session this COMPLETES the multipart upload first, then
        checks the object exists. Keeping completion and verification in one
        endpoint means every authoritative check (size, type, quota) has exactly
        one entry point — there is no second "finalize" path that could skip one
        of them.

        ``parts`` is required for a multipart session and ignored otherwise. A
        repeated verify finds ``multipart_upload_id`` already cleared and simply
        re-checks the assembled object, which keeps retries idempotent.
        """
        session = await self.get_upload_session(upload_id)

        if session.upload_mode == "multipart" and session.multipart_upload_id is not None:
            if not parts:
                raise MissingUploadPartsError(
                    "A multipart upload must be finalized with its part list."
                )
            # Ascending part order is required by the CompleteMultipartUpload
            # API; the client may report them in whatever order its concurrent
            # PUTs happened to finish.
            ordered = sorted(parts, key=lambda item: item[0])
            await self._storage.complete_multipart_upload(
                session.object_key, session.multipart_upload_id, ordered,
            )
            # Clear the id BEFORE anything else can fail, so a retry after a
            # later rejection does not try to complete an already-completed
            # upload (which the provider would reject as NoSuchUpload).
            session.multipart_upload_id = None

        if not await self._storage.object_exists(session.object_key):
            raise UploadVerificationError(f"Upload for session {upload_id} is not complete or failed.")

        session.status = "completed"
        # Persist the completed status so a repeated verify (double click or a
        # client retry) can tell the session was already finalised.
        await self._cache.set(upload_id, session.model_dump_json(), ttl=self._session_ttl(session))
        return session

    async def create_download_url(
        self,
        object_key: str,
        *,
        expires_in_minutes: int | None = None,
    ) -> str:
        """Generate a time-limited pre-signed URL for downloading an object.

        Args:
            object_key: The key of the stored object to download.
            expires_in_minutes: URL validity in minutes. Defaults to the
                service's configured TTL.

        Returns:
            A pre-signed GET URL the client can use to download the object.
        """
        ttl_minutes = expires_in_minutes or int(self._ttl.total_seconds() // 60)
        return self._storage.generate_get_url(object_key, ttl_minutes)

    async def _load_session_or_none(self, upload_id: str) -> UploadSession | None:
        """Read a session without raising when it is missing or unparseable."""
        data = await self._cache.get(upload_id)
        if not data:
            return None
        try:
            return UploadSession.model_validate_json(data)
        except Exception:  # noqa: BLE001 - a corrupt cache entry must not block a delete
            self._logger.warning("Discarding unparseable upload session %s", upload_id)
            return None

    def _generate_object_key(self, job_id: str, file_extension: str) -> str:
        """Create a secure, collision-resistant object path."""
        ext = normalize_extension(file_extension)
        if not ext or len(ext) > 20:
            ext = "file"
        secure_filename = f"{uuid4().hex}.{ext}"
        return sanitize_object_key(f"{job_id}/{secure_filename}")

