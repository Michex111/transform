from minio import Minio
from src.application.ports.contracts import FileStorageGateway
from src.infrastructure.config.settings import get_settings
from .minio_storage_adapter import MinioFileStorageAdapter
from typing import Optional
import logging

def get_storage(logger: Optional[logging.Logger] = None) -> FileStorageGateway:
    settings = get_settings()

    client = Minio(
        endpoint=settings.BACKBLAZE_ENDPOINT,
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=True,
    )

    return MinioFileStorageAdapter(
        bucket_name=settings.S3_BUCKET_NAME,
        s3_client=client,
        logger=logger
    )

