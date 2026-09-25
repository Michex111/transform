import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.api_key_service import APIKeyService
from src.application.services.conversion_service import ConversionService
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.application.ports.email_port import EmailPort
from src.application.ports.sms_port import SmsPort
from src.infrastructure.adapters.cache.redis_session_adapter import RedisSessionAdapter
from src.infrastructure.adapters.email import build_email_sender
from src.infrastructure.adapters.sms import build_sms_sender
from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStream
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventSubscriber
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.adapters.security.encryption import (
    FileEncryptionService,
    get_file_encryption_service as _build_encryption_service,
)
from src.infrastructure.adapters.storage.endpoint import normalize_endpoint
from src.infrastructure.adapters.storage.minio_storage_adapter import (
    MinioFileStorageAdapter,
    MinioUrlStorageAdapter,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session
from src.infrastructure.redis.client import create_redis_client


@lru_cache
def _shared_redis_client():
    return create_redis_client(get_settings().REDIS_URL.get_secret_value())


@lru_cache
def _shared_minio_client() -> Minio:
    settings = get_settings()
    return Minio(
        endpoint=normalize_endpoint(settings.BACKBLAZE_ENDPOINT),
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=settings.BACKBLAZE_USE_SSL,
    )


def get_job_queue_port() -> JobStream:
    return JobStream(redis_client=_shared_redis_client())


def get_conversion_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLConversionJobRepository:
    return SQLConversionJobRepository(session=db)


def get_user_file_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLUserFileRepository:
    return SQLUserFileRepository(session=db)


def get_user_folder_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLUserFolderRepository:
    return SQLUserFolderRepository(session=db)


def get_api_key_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLAPIKeyRepository:
    return SQLAPIKeyRepository(session=db)


def get_subscription_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLSubscriptionRepository:
    return SQLSubscriptionRepository(session=db)


def get_credit_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLCreditRepository:
    return SQLCreditRepository(session=db)


def get_api_key_service(
    repository: Annotated[SQLAPIKeyRepository, Depends(get_api_key_repository)],
) -> APIKeyService:
    return APIKeyService(repository=repository)


@lru_cache
def get_stripe_service() -> StripeService:
    # One client (and therefore one Stripe HTTP pool) per process instead of a
    # fresh StripeClient per request. StripeService builds its StripeClient
    # lazily and is stateless otherwise, so sharing it is safe.
    return StripeService()


@lru_cache
def get_email_sender() -> EmailPort:
    """The process-wide transactional email transport.

    Cached so the transport (and, for the Resend adapter, its connection pool
    configuration) is built once rather than per request. The transport is
    stateless between sends, so sharing it across requests is safe.

    Tests override this dependency with a capturing fake, which is how the
    verification flow is asserted without a provider or network.
    """
    return build_email_sender(get_settings())


@lru_cache
def get_sms_sender() -> SmsPort:
    """The process-wide transactional SMS transport.

    Cached for the same reason as ``get_email_sender``: the transport is built
    once and is stateless between sends. Tests override this dependency with a
    capturing fake, which is how the phone-verification flow is asserted
    without a provider or network.
    """
    return build_sms_sender(get_settings())


def get_event_subscriber() -> JobEventSubscriber:
    return JobEventSubscriber(redis_client=_shared_redis_client())


def get_conversion_service(
    queue_port: Annotated[JobStream, Depends(get_job_queue_port)],
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
) -> ConversionService:
    dispatcher = PriorityQueueDispatcher(
        queue_port=queue_port,
        router=QueuePriorityRouter(),
    )
    return ConversionService(
        queue_port=queue_port,
        db_repository=repository,
        queue_dispatcher=dispatcher,
    )


def get_minio_url_storage() -> MinioUrlStorageAdapter:
    settings = get_settings()
    return MinioUrlStorageAdapter(
        minio_client=_shared_minio_client(),
        bucket_name=settings.S3_BUCKET_NAME,
        ttl_minutes=settings.UPLOAD_URL_TTL_MINUTES,
    )


def get_minio_download_adapter() -> MinioFileStorageAdapter:
    """File storage adapter capable of opening streaming object reads."""
    return MinioFileStorageAdapter(
        bucket_name=get_settings().S3_BUCKET_NAME,
        s3_client=_shared_minio_client(),
    )


@lru_cache
def get_encryption_service() -> FileEncryptionService | None:
    """At-rest encryption service, or None when ENCRYPTION_MASTER_KEY is unset."""
    return _build_encryption_service()


def get_session_cache() -> RedisSessionAdapter:
    return RedisSessionAdapter(redis_client=_shared_redis_client())


def get_guest_token_cache() -> RedisSessionAdapter:
    """Redis-backed store for guest access tokens.

    Uses a distinct ``guest_token:`` prefix so guest token → job_id mappings
    never collide with upload sessions (which live under ``upload_session:``).
    """
    return RedisSessionAdapter(
        redis_client=_shared_redis_client(),
        prefix="guest_token:",
    )


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
        # Multipart configuration is injected rather than read lazily so the
        # process uses one set of tuned values (and so a deployment that raises
        # the ceiling also raises the part sizing that has to match it).
        large_ttl_minutes=settings.LARGE_UPLOAD_URL_TTL_MINUTES,
        multipart_threshold_bytes=settings.MULTIPART_THRESHOLD_BYTES,
        multipart_part_size_bytes=settings.MULTIPART_PART_SIZE_BYTES,
    )


def get_file_service(
    file_repository: Annotated[SQLUserFileRepository, Depends(get_user_file_repository)],
    folder_repository: Annotated[SQLUserFolderRepository, Depends(get_user_folder_repository)],
    storage: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    subscription_repository: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> FileService:
    return FileService(
        file_repository=file_repository,
        folder_repository=folder_repository,
        storage=storage,
        subscription_repository=subscription_repository,
    )
