from minio import Minio
from src.application.ports.contracts import StorageGateway
from src.infrastructure.config.settings import get_settings
from .s3_storage import MinioStorage
from typing import Optional
import logging

def get_storage(logger: Optional[logging.Logger] = None) -> StorageGateway:
    settings = get_settings()

    client = Minio(
        endpoint=settings.BACKBLAZE_ENDPOINT,
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=True,
    )

    return MinioStorage(
        bucket_name=settings.S3_BUCKET_NAME,
        s3_client=client,
        logger=logger
    )

