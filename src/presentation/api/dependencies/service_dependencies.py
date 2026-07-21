import logging
from typing import Annotated

from fastapi import Depends
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.infrastructure.adapters.cache.redis_session_adapter import RedisSessionAdapter
from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStream
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioUrlStorageAdapter
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session
from src.infrastructure.redis.client import create_redis_client


def get_job_queue_port() -> JobStream:
    settings = get_settings()
    redis_client = create_redis_client(settings.REDIS_URL.get_secret_value())
    return JobStream(redis_client=redis_client)


def get_conversion_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLConversionJobRepository:
    return SQLConversionJobRepository(session=db)


def get_conversion_service(
    queue_port: Annotated[JobStream, Depends(get_job_queue_port)],
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
) -> ConversionService:
    return ConversionService(queue_port=queue_port, db_repository=repository)


def get_minio_url_storage() -> MinioUrlStorageAdapter:
    settings = get_settings()
    client = Minio(
        endpoint=settings.BACKBLAZE_ENDPOINT,
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=True,
    )
    return MinioUrlStorageAdapter(
        minio_client=client,
        bucket_name=settings.S3_BUCKET_NAME,
        ttl_minutes=settings.UPLOAD_URL_TTL_MINUTES,
    )


def get_session_cache() -> RedisSessionAdapter:
    settings = get_settings()
    redis_client = create_redis_client(settings.REDIS_URL.get_secret_value())
    return RedisSessionAdapter(redis_client=redis_client)


def get_transfer_service(
    storage_port: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    cache_port: Annotated[RedisSessionAdapter, Depends(get_session_cache)],
) -> TransferService:
    settings = get_settings()
    return TransferService(
        storage_port=storage_port,
        cache_port=cache_port,
        ttl_minutes=settings.UPLOAD_URL_TTL_MINUTES,
        logger=logging.getLogger("file_converter_api"),
    )
