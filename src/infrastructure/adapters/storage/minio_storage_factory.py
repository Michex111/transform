from minio import Minio
from src.infrastructure.config.settings import get_settings
from .endpoint import normalize_endpoint
from .minio_storage_adapter import MinioFileStorageAdapter
from typing import Optional
import logging

def get_storage(logger: Optional[logging.Logger] = None) -> MinioFileStorageAdapter:
    settings = get_settings()

    client = Minio(
        endpoint=normalize_endpoint(settings.BACKBLAZE_ENDPOINT),
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=settings.BACKBLAZE_USE_SSL,
    )

    return MinioFileStorageAdapter(
        bucket_name=settings.S3_BUCKET_NAME,
        s3_client=client,
        logger=logger
    )

