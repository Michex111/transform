"""Apply a bucket CORS policy for browser uploads (Backblaze B2 native, S3).

The web app uploads files directly to object storage via presigned PUT URLs.
The browser first fires a CORS preflight request to the object-storage
endpoint; if the bucket has no CORS rule allowing the SPA origin, the upload
is rejected with a CORS error.

Backblaze B2 stores **native B2 CORS rules** and refuses the generic S3
``PutBucketCors`` API when native rules exist, so we use the ``b2sdk`` for
Backblaze and fall back to the S3 ``PutBucketCors`` API for generic S3/Minio.

This is invoked at application startup (and can be run standalone via
``python -m src.infrastructure.adapters.storage.cors``). Failures are
non-fatal so startup never blocks on CORS configuration.
"""

import logging

from src.infrastructure.config.settings import get_settings

logger = logging.getLogger(__name__)

# B2 native CORS operations the browser needs for direct uploads/downloads.
_B2_ALLOWED_OPERATIONS = ["s3_head", "s3_get", "s3_put", "s3_post"]
# S3-style methods for the generic S3 path.
_S3_ALLOWED_METHODS = ["GET", "PUT", "POST", "HEAD"]


def _is_backblaze(endpoint: str) -> bool:
    return "backblazeb2.com" in (endpoint or "").lower() or "b2" in (endpoint or "").lower()


def _build_b2_cors(origins: list[str]) -> list[dict]:
    """Build the Backblaze B2 native CORS rules payload.

    The b2sdk expects ``cors_rules`` to be a plain **list** of rule dicts (the
    raw B2 API accepts an array). Each rule requires a ``corsRuleName`` and an
    ``allowedOrigins`` glob (e.g. ``http://localhost:*``). We rewrite each
    configured origin to its scheme+host wildcard so the SPA is allowed
    regardless of the local port.
    """
    # B2 only permits a wildcard at the START of an origin (e.g. ``*`` or
    # ``https://*.example.com``); it rejects ``*`` after the hostname (e.g.
    # ``http://localhost:*``). The browser sends the exact origin (scheme +
    # host + port), so we allow the configured origins verbatim.
    allowed_origins = list(origins)

    return [
        {
            "corsRuleName": "transform-spa-upload",
            "allowedOrigins": allowed_origins,
            "allowedOperations": _B2_ALLOWED_OPERATIONS,
            "allowedHeaders": ["*"],
            "exposeHeaders": ["ETag", "content-length", "x-bz-content-sha1"],
            "maxAgeSeconds": 3600,
        }
    ]


def _build_s3_cors(origins: list[str]) -> list[dict]:
    """Build generic S3 CORS rules for the given origins."""
    return [
        {
            "AllowedOrigins": origins,
            "AllowedMethods": _S3_ALLOWED_METHODS,
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
            "MaxAgeSeconds": 3600,
        }
    ]


def apply_bucket_cors(origins: list[str] | None = None) -> None:
    """Set the object-storage bucket CORS policy for the configured origins.

    Args:
        origins: Explicit allow-list. When ``None``, reads
            ``settings.S3_CORS_ALLOWED_ORIGINS``.
    """
    settings = get_settings()
    allowed = origins if origins is not None else (settings.S3_CORS_ALLOWED_ORIGINS or [])
    if not allowed:
        logger.info("No storage bucket CORS origins configured — skipping bucket CORS.")
        return

    endpoint = (settings.BACKBLAZE_ENDPOINT or "").lower()

    if _is_backblaze(endpoint):
        _apply_b2_cors(allowed)
    else:
        _apply_s3_cors(allowed)


def _apply_b2_cors(allowed_origins: list[str]) -> None:
    """Apply Backblaze B2 native CORS rules via the b2sdk."""
    settings = get_settings()
    try:
        from b2sdk.v2 import B2Api, InMemoryAccountInfo
        from b2sdk.v2.exception import B2Error

        info = InMemoryAccountInfo()
        api = B2Api(info)
        api.authorize_account(
            "production",
            settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
            settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        )
        bucket = api.get_bucket_by_name(settings.S3_BUCKET_NAME)
        cors_rules = _build_b2_cors(allowed_origins)
        bucket.update(bucket_type=bucket.type_, cors_rules=cors_rules)
        logger.info("Applied B2 bucket CORS rules to %s", settings.S3_BUCKET_NAME)
    except B2Error as e:
        logger.warning("Failed to apply B2 bucket CORS: %s", e)
    except Exception as e:  # noqa: BLE001 — startup must not crash
        logger.warning("Failed to apply B2 bucket CORS: %s", e)


def _apply_s3_cors(allowed_origins: list[str]) -> None:
    """Apply a generic S3-compatible CORS policy (Minio, AWS S3)."""
    settings = get_settings()
    import boto3
    from botocore.config import Config

    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.BACKBLAZE_ENDPOINT,
            aws_access_key_id=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
            config=Config(signature_version="s3v4"),
        )
        client.put_bucket_cors(
            Bucket=settings.S3_BUCKET_NAME,
            CORSConfiguration={"CORSRules": _build_s3_cors(allowed_origins)},
        )
        logger.info("Applied S3 bucket CORS to %s", settings.S3_BUCKET_NAME)
    except Exception as e:  # noqa: BLE001 — startup must not crash
        logger.warning("Failed to apply S3 bucket CORS: %s", e)


if __name__ == "__main__":  # pragma: no cover — manual CLI use
    logging.basicConfig(level=logging.INFO)
    apply_bucket_cors()
