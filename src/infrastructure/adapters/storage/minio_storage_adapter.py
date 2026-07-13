import asyncio

from minio import Minio
from minio.error import S3Error
from datetime import timedelta
from pathlib import Path
from typing import Optional
from src.infrastructure.config.settings import get_settings
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