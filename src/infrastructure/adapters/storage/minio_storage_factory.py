from minio import Minio
from src.infrastructure.config.settings import get_settings
from .endpoint import normalize_endpoint
from .minio_storage_adapter import MinioFileStorageAdapter
from typing import Optional
import logging
import urllib3

# The worker's storage calls are blocking and run one job at a time, so a
# stalled socket must not hold the loop for long. minio-py does apply its own
# defaults, but they are a 5-minute CONNECT timeout (per attempt, with 5
# retries) — long enough that a firewalled endpoint parks the worker for ~25
# minutes. Tighten the connect timeout while keeping a generous read timeout
# for genuinely large uploads/downloads.
_HTTP_CONNECT_TIMEOUT_SECONDS = 30
_HTTP_READ_TIMEOUT_SECONDS = 300


def _build_http_client() -> urllib3.PoolManager:
    """urllib3 pool with explicit connect/read timeouts, mirroring minio's."""
    return urllib3.PoolManager(
        timeout=urllib3.Timeout(
            connect=_HTTP_CONNECT_TIMEOUT_SECONDS,
            read=_HTTP_READ_TIMEOUT_SECONDS,
        ),
        maxsize=10,
        retries=urllib3.Retry(
            total=5,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504],
        ),
    )


def get_storage(logger: Optional[logging.Logger] = None) -> MinioFileStorageAdapter:
    settings = get_settings()

    client = Minio(
        endpoint=normalize_endpoint(settings.BACKBLAZE_ENDPOINT),
        access_key=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
        secret_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        secure=settings.BACKBLAZE_USE_SSL,
        http_client=_build_http_client(),
    )

    return MinioFileStorageAdapter(
        bucket_name=settings.S3_BUCKET_NAME,
        s3_client=client,
        logger=logger
    )

