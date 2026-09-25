from typing import Protocol

class StorageUrlGateway(Protocol):
    """
    Protocol for object storage operations.
    """

    def generate_put_url(self, object_key: str) -> str:
        """
        Generate a pre-signed URL for uploading an object.

        Args:
            object_key: The key of the object to be uploaded.

        Returns:
            A pre-signed URL for uploading the object.
        """
        ...

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        """
        Generate a pre-signed URL for downloading an object.

        Args:
            object_key: The key of the object to be downloaded.
            expires_in_minutes: How long the download URL should remain valid.

        Returns:
            A pre-signed URL for downloading the object.
        """
        ...

    async def object_exists(self, object_key: str) -> bool:
        """
        Check if an object exists in the storage.

        Args:
            object_key: The key of the object to check.


        Returns:
            bool: True if the object exists, False otherwise.
        """
        ...

    # ------------------------------------------------------------------
    # Multipart upload
    # ------------------------------------------------------------------
    # A single presigned PUT cannot carry a multi-gigabyte file: providers cap
    # it (5 GiB on B2/S3), the URL expires mid-transfer, and a dropped
    # connection means starting over. Multipart upload solves all three — parts
    # are uploaded independently, each with its own presigned URL, and only the
    # tiny "complete" call needs the whole transfer to have succeeded.

    def create_multipart_upload(self, object_key: str) -> str:
        """
        Start a multipart upload.

        Args:
            object_key: The key the assembled object will be stored under.

        Returns:
            The provider's multipart upload id, which must be persisted with
            the session and passed to every subsequent call.
        """
        ...

    def generate_part_upload_url(
        self,
        object_key: str,
        part_number: int,
        upload_id: str,
        expires_in_minutes: int,
    ) -> str:
        """
        Presign a PUT URL for ONE part of a multipart upload.

        Args:
            object_key: The key of the object being assembled.
            part_number: 1-based part index.
            upload_id: The id returned by :meth:`create_multipart_upload`.
            expires_in_minutes: How long this single part URL stays valid.

        Returns:
            A presigned PUT URL for that part.
        """
        ...

    async def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[tuple[int, str]],
    ) -> None:
        """
        Assemble the uploaded parts into the final object.

        Args:
            object_key: The key of the object being assembled.
            upload_id: The id returned by :meth:`create_multipart_upload`.
            parts: ``(part_number, etag)`` pairs in ascending part order. The
                etag is the raw value from the part PUT's ``ETag`` header; the
                adapter is responsible for any quoting the provider expects.
        """
        ...

    async def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        """
        Abort a multipart upload and discard every part already uploaded.

        Must be safe to call on an upload that no longer exists: it is used on
        the cleanup path, where raising would mask the original failure and, for
        a session delete, leave the client unable to release the upload.

        Args:
            object_key: The key of the object being assembled.
            upload_id: The id returned by :meth:`create_multipart_upload`.
        """
        ...

