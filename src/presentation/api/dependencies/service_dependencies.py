import logging
from typing import Annotated

from fastapi import Depends
from minio import Minio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.ports.minio_port import MinioObjectGateway
from src.application.services.conversion_access_service import ConversionAccessService
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.application.services.user_job_query_service import UserJobQueryService
from src.infrastructure.adapters.cache.redis_session_adapter import RedisSessionAdapter
from src.infrastructure.adapters.queues.redis_priority_queue_adapter import RedisPriorityQueueAdapter
from src.infrastructure.adapters.queues.redis_rate_limiter_adapter import RedisRateLimiterAdapter
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.storage.minio_object_gateway_adapter import MinioObjectGatewayAdapter
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioUrlStorageAdapter
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session
from src.infrastructure.redis.client import create_redis_client


def get_redis_client() -> Redis:
    """Provides shared Redis client dependency."""
    settings = get_settings()
    return create_redis_client(settings.REDIS_URL.get_secret_value())


def get_conversion_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLConversionJobRepository:
    """Provides conversion repository dependency."""
    return SQLConversionJobRepository(session=db)


def get_subscription_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLSubscriptionRepository:
    """Provides subscription/credit repository dependency."""
    return SQLSubscriptionRepository(session=db)


def get_conversion_service(
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
) -> ConversionService:
    """Provides conversion creation service.

    Uses a no-op queue adapter path for create/get operations; dispatching is handled separately
    by PriorityQueueDispatcher for tier-based stream routing.
    """

    class _NoOpQueuePort:
        async def push_job(self, job):  # pragma: no cover - defensive path
            del job
            return None

    return ConversionService(queue_port=_NoOpQueuePort(), db_repository=repository)  # type: ignore[arg-type]


def get_minio_url_storage() -> MinioUrlStorageAdapter:
    """Provides MinIO URL adapter dependency."""
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


def get_minio_object_gateway(
    storage: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
) -> MinioObjectGateway:
    """Provides object gateway abstraction for presigned GET URLs."""
    return MinioObjectGatewayAdapter(storage)


def get_session_cache(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> RedisSessionAdapter:
    """Provides Redis-backed session cache dependency."""
    return RedisSessionAdapter(redis_client=redis_client)


def get_transfer_service(
    storage_port: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    cache_port: Annotated[RedisSessionAdapter, Depends(get_session_cache)],
) -> TransferService:
    """Provides file transfer service dependency."""
    settings = get_settings()
    return TransferService(
        storage_port=storage_port,
        cache_port=cache_port,
        ttl_minutes=settings.UPLOAD_URL_TTL_MINUTES,
        logger=logging.getLogger("file_converter_api"),
    )


def get_queue_priority_router() -> QueuePriorityRouter:
    """Provides tier to stream router dependency."""
    return QueuePriorityRouter()


def get_rate_limiter_port(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> RedisRateLimiterAdapter:
    """Provides Redis-backed rate limiter dependency."""
    return RedisRateLimiterAdapter(redis_client=redis_client)


def get_priority_queue_dispatcher(
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    queue_router: Annotated[QueuePriorityRouter, Depends(get_queue_priority_router)],
) -> PriorityQueueDispatcher:
    """Provides dispatcher that routes jobs by subscription tier."""
    return PriorityQueueDispatcher(
        queue_port=RedisPriorityQueueAdapter(redis_client=redis_client),
        queue_router=queue_router,
    )


def get_conversion_access_service(
    subscription_repository: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    rate_limiter: Annotated[RedisRateLimiterAdapter, Depends(get_rate_limiter_port)],
    queue_router: Annotated[QueuePriorityRouter, Depends(get_queue_priority_router)],
) -> ConversionAccessService:
    """Provides conversion access service for credits, quotas, and guest throttling."""
    settings = get_settings()
    return ConversionAccessService(
        subscription_repository=subscription_repository,
        credit_repository=subscription_repository,
        rate_limiter=rate_limiter,
        queue_router=queue_router,
        guest_conversion_limit=settings.GUEST_RATE_LIMIT_LIMIT,
        guest_window_seconds=settings.GUEST_RATE_LIMIT_WINDOW_SECONDS,
    )


def get_user_job_query_service(
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    object_gateway: Annotated[MinioObjectGateway, Depends(get_minio_object_gateway)],
) -> UserJobQueryService:
    """Provides history and active queue query service."""
    settings = get_settings()
    return UserJobQueryService(
        repository=repository,
        object_gateway=object_gateway,
        download_url_ttl_minutes=settings.UPLOAD_URL_TTL_MINUTES,
    )
