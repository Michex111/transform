from minio import Minio
from minio.error import S3Error
from pathlib import Path
from typing import Optional
import logging


class MinioStorage:
    def __init__(self, bucket_name, s3_client: Minio, logger: Optional[logging.Logger] = None) -> None:
        self.bucket_name = bucket_name
        self.s3_client = s3_client
        self.logger = logger

    def download(self, key: str, dest_path: Path):
        try:
            self.s3_client.fget_object(self.bucket_name, key, str(dest_path))
        except S3Error as e:
            if self.logger:
                self.logger.error("minio_download_failed", extra={"key": key, "error": str(e)})
            raise

    def upload(self, target_key: str, source_path: Path):
        try:
            self.s3_client.fput_object(self.bucket_name, target_key, str(source_path))
        except S3Error as e:
            if self.logger:
                self.logger.error("minio_upload_failed", extra={"key": target_key, "error": str(e)})



