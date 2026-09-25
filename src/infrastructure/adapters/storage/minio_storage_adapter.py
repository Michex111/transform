import asyncio

from minio import Minio
from minio.datatypes import Part
from minio.error import S3Error
from datetime import timedelta
from pathlib import Path
from typing import Optional
from src.infrastructure.adapters.storage.exceptions import (
    ObjectNotFoundError,
    StorageOperationError,
    StoragePermissionError,
)
import logging


class MinioFileStorageAdapter:
    def __init__(self, bucket_name, s3_client: Minio, logger: Optional[logging.Logger] = None) -> None:
        self.bucket_name = bucket_name
        self.s3_client = s3_client
        self.logger = logger or logging.getLogger(__name__)

    def download(self, key: str, dest_path: Path):
        try:
            self.s3_client.fget_object(self.bucket_name, key, str(dest_path))
        except S3Error as e:
            self.logger.error("minio_download_failed", extra={"key": key, "error": str(e)})
            raise

    def upload(self, target_key: str, source_path: Path):
        try:
            self.s3_client.fput_object(self.bucket_name, target_key, str(source_path))
        except S3Error as e:
            self.logger.error("minio_upload_failed", extra={"key": target_key, "error": str(e)})
            raise

    def remove_object(self, key: str) -> bool:
        """Delete an object. Returns False when it did not exist."""
        try:
            self.s3_client.remove_object(self.bucket_name, key)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            self.logger.error("minio_remove_failed", extra={"key": key, "error": str(e)})
            raise

    def list_objects(self, prefix: str) -> list[dict]:
        """List objects under a prefix with their metadata."""
        objects = self.s3_client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
        return [
            {
                "object_name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified,
            }
            for obj in objects
        ]

    def get_object_stream(self, key: str):
        """
        Open a streaming read handle for an object.

        The caller must close the returned response object (``.close()``).
        """
        try:
            return self.s3_client.get_object(self.bucket_name, key)
        except S3Error as e:
            self.logger.error("minio_get_stream_failed", extra={"key": key, "error": str(e)})
            raise

class MinioUrlStorageAdapter:
    def __init__(self, minio_client: Minio, bucket_name: str, ttl_minutes: int, logger: Optional[logging.Logger] = None):
        self._minio_client = minio_client
        self._bucket_name = bucket_name
        self._ttl = timedelta(minutes=ttl_minutes)
        self._logger = logger or logging.getLogger(__name__)

    def generate_put_url(self, object_key: str) -> str:
        """
        Generates a MinIO presigned PUT URL for uploading an object.

        Args:
            object_key: The path of the stored file.
            expires_in_minutes: How long the upload URL should remain valid.

        Returns:
            str: The presigned PUT URL.
            
        Raises:
            StorageOperationError: If MinIO encounters an issue.
        """
        try: 
            return self._minio_client.presigned_put_object(
                bucket_name=self._bucket_name,
                object_name=object_key,
                expires=self._ttl
            )
        except S3Error as e:
            self._logger.error("minio_generate_upload_url_failed", extra={"key": object_key, "error": str(e)})
            if e.code == "NoSuchKey":
                raise ObjectNotFoundError(f"Object '{object_key}' not found in bucket '{self._bucket_name}'.")
            
            if e.code == "AccessDenied":
                raise StoragePermissionError(f"Access denied for object '{object_key}' in bucket '{self._bucket_name}'.")
            
            raise StorageOperationError() from e
        
    def generate_get_url(self, object_key: str, expires_in_minutes: int = 60) -> str:
        """
        Generates a MinIO presigned GET URL for downloading an object.

        Args:
            object_key: The path of the stored file.
            expires_in_minutes: How long the download URL should remain valid.

        Returns:
            str: The presigned GET URL.

        Raises:
            StorageOperationError: If MinIO encounters an issue.
        """
        try:
            return self._minio_client.presigned_get_object(
                bucket_name=self._bucket_name,
                object_name=object_key,
                expires=timedelta(minutes=expires_in_minutes)
            )
        except S3Error as e:
            self._logger.error("minio_generate_download_url_failed", extra={"key": object_key, "error": str(e)})
            if e.code == "NoSuchKey":
                raise ObjectNotFoundError(f"Object '{object_key}' not found in bucket '{self._bucket_name}'.")
            
            if e.code == "AccessDenied":
                raise StoragePermissionError(f"Access denied for object '{object_key}' in bucket '{self._bucket_name}'.")
            
            raise StorageOperationError() from e

    # ------------------------------------------------------------------
    # Multipart upload
    # ------------------------------------------------------------------
    # These call the SDK's ``_``-prefixed multipart helpers. That is not an
    # oversight: minio-py (7.2.x) exposes multipart upload only through
    # ``fput_object``/``put_object``, which stream the bytes *through* this
    # process. That defeats the entire point of a presigned direct upload (the
    # browser would have to hand 5 GiB to the API first), so we drive the
    # underlying S3 API calls directly. The helpers are thin, stable wrappers
    # over ``CreateMultipartUpload``/``CompleteMultipartUpload``/
    # ``AbortMultipartUpload`` and are what the SDK's own public methods use.
    #
    # The presigned part URL uses the public ``get_presigned_url`` with
    # ``extra_query_params``: SigV4 signs the full canonical query string, so
    # ``partNumber`` + ``uploadId`` are covered by the signature exactly as a
    # native ``UploadPart`` presign would be. (Verified against the installed
    # minio 7.2.20: ``get_presigned_url(method, bucket_name, object_name,
    # expires, ..., extra_query_params)`` and ``presign_v4`` signs
    # ``url.query`` in full.)

    def create_multipart_upload(self, object_key: str) -> str:
        """Start a multipart upload and return the provider upload id."""
        try:
            return self._minio_client._create_multipart_upload(
                self._bucket_name,
                object_key,
                # The SDK mutates this dict and defaults Content-Type itself.
                {"Content-Type": "application/octet-stream"},
            )
        except S3Error as e:
            self._logger.error(
                "minio_create_multipart_failed", extra={"key": object_key, "error": str(e)}
            )
            raise StorageOperationError() from e

    def generate_part_upload_url(
        self,
        object_key: str,
        part_number: int,
        upload_id: str,
        expires_in_minutes: int,
    ) -> str:
        """Presign a PUT URL for one part of a multipart upload.

        The URL is valid only for ``object_key`` + ``upload_id`` +
        ``part_number``: the query parameters are inside the signature, so a
        part URL cannot be replayed against a different part or upload.
        """
        try:
            return self._minio_client.get_presigned_url(
                "PUT",
                self._bucket_name,
                object_key,
                expires=timedelta(minutes=expires_in_minutes),
                extra_query_params={
                    "partNumber": str(part_number),
                    "uploadId": upload_id,
                },
            )
        except S3Error as e:
            self._logger.error(
                "minio_generate_part_url_failed",
                extra={"key": object_key, "part_number": part_number, "error": str(e)},
            )
            raise StorageOperationError() from e

    async def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[tuple[int, str]],
    ) -> None:
        """Assemble the uploaded parts into the final object.

        ``parts`` must be in ascending part order and carry the etags exactly as
        returned by each part PUT. Any surrounding double quotes are stripped
        here because the SDK re-adds them when it writes the
        ``CompleteMultipartUpload`` XML body; passing a quoted etag through
        would produce ``""etag""`` and the provider would reject the completion.
        """

        def _complete() -> None:
            try:
                self._minio_client._complete_multipart_upload(
                    self._bucket_name,
                    object_key,
                    upload_id,
                    [
                        Part(part_number=n, etag=etag.strip('"'))
                        for n, etag in parts
                    ],
                )
            except S3Error as e:
                self._logger.error(
                    "minio_complete_multipart_failed",
                    extra={"key": object_key, "error": str(e)},
                )
                raise StorageOperationError() from e

        await asyncio.to_thread(_complete)

    async def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        """Abort a multipart upload, discarding every part already uploaded.

        Best-effort: a missing upload (``NoSuchUpload``) is not an error, because
        this runs on cleanup paths where the interesting failure already
        happened and raising here would mask it. For a session delete that would
        also leave the client unable to release a stuck upload.
        """

        def _abort() -> None:
            try:
                self._minio_client._abort_multipart_upload(
                    self._bucket_name, object_key, upload_id,
                )
            except S3Error as e:
                if e.code in ("NoSuchUpload", "NoSuchKey"):
                    return
                self._logger.error(
                    "minio_abort_multipart_failed",
                    extra={"key": object_key, "error": str(e)},
                )
                raise StorageOperationError() from e

        await asyncio.to_thread(_abort)

    async def object_exists(self, object_key: str) -> bool:
        """
        Check if an object exists in the MinIO bucket.

        Args:
            object_key: The path of the stored file.

        Returns:
            bool: True if the object exists, False otherwise.

        Raises:
            StorageOperationError: If MinIO encounters an issue.
        """
        def _stat():
            try:
                self._minio_client.stat_object(self._bucket_name, object_key)
                return True
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return False
                self._logger.error("minio_check_object_exists_failed", extra={"key": object_key, "error": str(e)})
                raise StorageOperationError() from e
            
        return await asyncio.to_thread(_stat)

    async def stat_object(self, object_key: str) -> dict | None:
        """
        Fetch metadata for an object.

        Returns:
            A dict with ``size`` and ``content_type``, or None when missing.
        """
        def _stat():
            try:
                obj = self._minio_client.stat_object(self._bucket_name, object_key)
                return {
                    "size": obj.size,
                    "content_type": obj.content_type,
                }
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return None
                self._logger.error("minio_stat_object_failed", extra={"key": object_key, "error": str(e)})
                raise StorageOperationError() from e

        return await asyncio.to_thread(_stat)

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        """Read the first ``max_bytes`` of an object (for magic-byte checks)."""
        def _read():
            try:
                response = self._minio_client.get_object(self._bucket_name, object_key)
                try:
                    return response.read(max_bytes)
                finally:
                    response.close()
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return b""
                self._logger.error("minio_read_head_failed", extra={"key": object_key, "error": str(e)})
                raise StorageOperationError() from e

        return await asyncio.to_thread(_read)

    async def remove_object(self, object_key: str) -> bool:
        """
        Delete an object from the MinIO bucket.

        Args:
            object_key: The S3 key of the object to delete.

        Returns:
            True if the object was deleted, False if it didn't exist.
        """
        def _remove():
            try:
                self._minio_client.remove_object(self._bucket_name, object_key)
                return True
            except S3Error as e:
                if e.code == "NoSuchKey":
                    return False
                self._logger.error("minio_remove_object_failed", extra={"key": object_key, "error": str(e)})
                raise StorageOperationError() from e

        return await asyncio.to_thread(_remove)

    async def list_objects(self, prefix: str) -> list[dict]:
        """
        List objects under a prefix in the bucket.

        Args:
            prefix: S3 key prefix to list under (e.g. 'user_uploads/42/').

        Returns:
            List of dicts with keys: object_name, size, last_modified.
        """
        def _list():
            objects = self._minio_client.list_objects(
                self._bucket_name, prefix=prefix, recursive=True,
            )
            return [
                {
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified,
                }
                for obj in objects
            ]

        return await asyncio.to_thread(_list)