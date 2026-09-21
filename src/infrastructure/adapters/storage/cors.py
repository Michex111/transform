"""Apply a bucket CORS policy for browser uploads (Backblaze B2 native, S3).

The web app uploads files directly to object storage via presigned PUT URLs.
The browser first fires a CORS preflight request to the object-storage
endpoint; if the bucket has no CORS rule allowing the SPA origin, the request
is rejected with a bare ``403`` carrying **no** CORS headers, which the browser
surfaces to the SPA as an opaque network failure (the UI shows a generic
"object storage" error). Nothing appears in the API logs, because the request
never reaches the API.

Backblaze B2 stores **native B2 CORS rules** and refuses the generic S3
``PutBucketCors`` API when native rules exist, so we use the ``b2sdk`` for
Backblaze and fall back to the S3 ``PutBucketCors`` API for generic S3/Minio.
``b2sdk`` is therefore a real runtime dependency (see ``pyproject.toml``):
without it the container degrades to an API that Backblaze rejects, and the
stored rules go stale without anyone noticing.

**Bucket CORS is bucket-global.** There is exactly one rule set per bucket and
it is rewritten on every application start, so whichever process boots last
wins. A local dev server writing only ``localhost`` origins would delete the
deployed SPA's origin. :data:`ALWAYS_ALLOWED_ORIGINS` pins the baseline that
every environment must preserve, and :func:`missing_bucket_cors_origins` reads
the rules back so a mismatch is reported instead of silently breaking uploads.

Invoked at application startup, or standalone::

    python -m src.infrastructure.adapters.storage.cors          # apply
    python -m src.infrastructure.adapters.storage.cors --check   # verify only

Failures are non-fatal at startup so boot never blocks on CORS configuration,
but they are logged at ``ERROR`` with the action required to fix them.
"""

import logging

from src.infrastructure.config.settings import get_settings

logger = logging.getLogger(__name__)

# B2 native CORS operations the browser needs for direct uploads/downloads.
_B2_ALLOWED_OPERATIONS = ["s3_head", "s3_get", "s3_put", "s3_post"]
# S3-style methods for the generic S3 path.
_S3_ALLOWED_METHODS = ["GET", "PUT", "POST", "HEAD"]

# Baseline origins that must ALWAYS survive a CORS rewrite.
#
# Rationale: the bucket holds ONE rule set shared by every deployment, and each
# environment rewrites it from its own configuration at startup. Pinning the
# known app origins here makes the write idempotent across environments, so a
# local dev boot can never remove the deployed SPA's origin — the exact failure
# that broke production uploads. Any additional deployed origin must be added to
# ``S3_CORS_ALLOWED_ORIGINS`` in *every* environment (or pinned here).
ALWAYS_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",  # Vite dev server
    "http://localhost:5174",  # Vite dev server (alternate port)
    "http://localhost:8000",  # API / legacy SPA origin
    "https://transform-web.onrender.com",  # deployed SPA (Render static site)
)


def _is_backblaze(endpoint: str) -> bool:
    return "backblazeb2.com" in (endpoint or "").lower() or "b2" in (endpoint or "").lower()


def _normalize_origins(origins) -> list[str]:
    """Trim, drop blanks and de-duplicate while preserving order."""
    normalized: list[str] = []
    for origin in origins or []:
        if not isinstance(origin, str):
            continue
        origin = origin.strip()
        if origin and origin not in normalized:
            normalized.append(origin)
    return normalized


def _configured_origins() -> list[str]:
    """The origins this process must keep allowed in the bucket.

    Union of ``S3_CORS_ALLOWED_ORIGINS``, ``ALLOWED_ORIGINS`` (the two settings
    drift apart easily and both are needed for direct uploads) and the
    :data:`ALWAYS_ALLOWED_ORIGINS` baseline.
    """
    settings = get_settings()
    return _normalize_origins(
        [
            *(settings.S3_CORS_ALLOWED_ORIGINS or []),
            *(settings.ALLOWED_ORIGINS or []),
            *ALWAYS_ALLOWED_ORIGINS,
        ]
    )


def _origins_from_b2_rules(rules: list[dict] | None) -> list[str]:
    """Flatten the ``allowedOrigins`` of every B2 CORS rule."""
    origins: list[str] = []
    for rule in rules or []:
        for origin in rule.get("allowedOrigins") or []:
            if origin not in origins:
                origins.append(origin)
    return origins


def _missing_origins(expected: list[str], actual: list[str] | None) -> list[str]:
    """Origins in ``expected`` that the bucket does not currently allow.

    ``None`` for ``actual`` means "the rules could not be read", which is
    reported as missing so a failed verification is never mistaken for success.
    """
    if actual is None:
        return list(expected)
    return [origin for origin in expected if origin not in actual]


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


def apply_bucket_cors(origins: list[str] | None = None) -> list[str]:
    """Set the object-storage bucket CORS policy for the configured origins.

    Args:
        origins: Explicit allow-list. When ``None``, uses the origins configured
            via ``S3_CORS_ALLOWED_ORIGINS`` / ``ALLOWED_ORIGINS`` merged with the
            :data:`ALWAYS_ALLOWED_ORIGINS` baseline.

    Returns:
        The origins that were written (empty when the call was a no-op).
    """
    settings = get_settings()
    allowed = _configured_origins() if origins is None else _normalize_origins(origins)
    if not allowed:
        logger.info("No storage bucket CORS origins configured — skipping bucket CORS.")
        return []

    logger.info(
        "Applying object-storage bucket CORS for %d origin(s): %s",
        len(allowed),
        ", ".join(allowed),
    )

    endpoint = (settings.BACKBLAZE_ENDPOINT or "").lower()

    if _is_backblaze(endpoint):
        _apply_b2_cors(allowed)
    else:
        _apply_s3_cors(allowed)
    return allowed


def _apply_b2_cors(allowed_origins: list[str]) -> None:
    """Apply Backblaze B2 native CORS rules via the b2sdk.

    ``b2sdk`` is a declared dependency, but if the import still fails we must not
    crash: importing it *inside* the ``try`` that also contains ``except
    B2Error`` was a trap — with the module absent, ``B2Error`` was never bound
    and evaluating the handler raised ``UnboundLocalError``, masking the real
    cause. Import it in its own guarded block instead.

    The S3 ``PutBucketCors`` fallback is attempted, but Backblaze rejects it once
    native CORS rules exist, so a failure here is logged at ``ERROR`` and the
    rules are read back afterwards to confirm the origins actually landed.
    """
    settings = get_settings()

    try:
        from typing import cast

        from b2sdk.v2 import AbstractAccountInfo, B2Api, InMemoryAccountInfo
        from b2sdk.v2.exception import B2Error
    except ImportError as exc:
        # Backblaze refuses the S3 PutBucketCors API once native CORS rules
        # exist, so this fallback is unreliable. It used to be a quiet warning,
        # which is exactly how the bucket ended up allowing only localhost while
        # every production upload failed. Keep trying, but make it loud.
        logger.error(
            "b2sdk is not installed (%s), so Backblaze-native CORS rules cannot "
            "be written. Backblaze rejects the S3 PutBucketCors fallback when "
            "native rules already exist, which leaves the bucket policy stale "
            "and breaks browser uploads. Fix: install the declared 'b2sdk' "
            "dependency in the image running the API.",
            exc,
        )
        _apply_s3_cors(allowed_origins)
        return

    try:
        info = cast(AbstractAccountInfo, InMemoryAccountInfo())
        api = B2Api(info)
        api.authorize_account(
            "production",
            settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
            settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        )
        bucket = api.get_bucket_by_name(settings.S3_BUCKET_NAME)
        cors_rules = _build_b2_cors(allowed_origins)
        # b2sdk's stub types ``cors_rules`` as ``dict | None`` but the B2 API
        # accepts a plain list of rule dicts (see ``_build_b2_cors``). Cast to
        # the declared type to satisfy the checker; runtime accepts the list.
        bucket.update(bucket_type=bucket.type_, cors_rules=cast(dict, cors_rules))
        logger.info("Applied B2 bucket CORS rules to %s", settings.S3_BUCKET_NAME)

        # Read the rules back. A successful update call is not proof that the
        # origin is allowed, and a wrong/missing origin otherwise only surfaces
        # much later as an unexplained browser upload failure.
        missing = _missing_origins(
            allowed_origins, _b2_cors_origins(api, settings.S3_BUCKET_NAME)
        )
        if missing:
            logger.error(
                "Bucket %s did not record these CORS origins: %s — browser "
                "uploads from them will fail the preflight with 403.",
                settings.S3_BUCKET_NAME,
                ", ".join(missing),
            )
        else:
            logger.info(
                "Verified %d bucket CORS origin(s) on %s",
                len(allowed_origins),
                settings.S3_BUCKET_NAME,
            )
    except B2Error as e:
        logger.error("Failed to apply B2 bucket CORS: %s", e)
    except Exception as e:  # noqa: BLE001 — startup must not crash
        logger.error("Failed to apply B2 bucket CORS: %s", e)


def _b2_cors_origins(api, bucket_name: str) -> list[str] | None:
    """Read the bucket's current B2 CORS origins (``None`` when unavailable)."""
    try:
        bucket = api.get_bucket_by_name(bucket_name)
        return _origins_from_b2_rules(bucket.as_dict().get("corsRules"))
    except Exception as e:  # noqa: BLE001 — read-back is best-effort
        logger.warning("Could not read back bucket CORS rules: %s", e)
        return None


def missing_bucket_cors_origins(origins: list[str] | None = None) -> list[str] | None:
    """Report expected bucket CORS origins that are NOT currently allowed.

    Returns an empty list when every expected origin is allowed (direct browser
    uploads from them will preflight successfully), or ``None`` when the check
    could not run (non-Backblaze endpoint, or ``b2sdk`` unavailable).
    """
    settings = get_settings()
    expected = _configured_origins() if origins is None else _normalize_origins(origins)

    if not _is_backblaze(settings.BACKBLAZE_ENDPOINT or ""):
        logger.warning("Bucket CORS verification is only implemented for Backblaze B2.")
        return None

    try:
        from typing import cast

        from b2sdk.v2 import AbstractAccountInfo, B2Api, InMemoryAccountInfo
    except ImportError as exc:
        logger.error("b2sdk is not installed (%s) — cannot verify bucket CORS.", exc)
        return None

    try:
        info = cast(AbstractAccountInfo, InMemoryAccountInfo())
        api = B2Api(info)
        api.authorize_account(
            "production",
            settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
            settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
        )
    except Exception as e:  # noqa: BLE001 — verification is best-effort
        logger.warning("Could not read bucket CORS rules: %s", e)
        return None

    return _missing_origins(expected, _b2_cors_origins(api, settings.S3_BUCKET_NAME))


def _derive_region(endpoint: str) -> str:
    """Derive the SigV4 region from an S3-compatible endpoint.

    Backblaze rejects a signature whose region does not match the endpoint
    (e.g. ``s3.us-east-005.backblazeb2.com`` requires ``us-east-005``), and
    boto3 would otherwise default to ``us-east-1`` and fail with a signature
    error. Falls back to ``us-east-1`` when the endpoint carries no region.
    """
    import re

    host = (endpoint or "").lower()
    match = re.search(r"\b([a-z]{2}-[a-z]+-\d+)\.", host) or re.search(
        r"s3[.-]([a-z]{2}-[a-z]+-\d+)", host
    )
    return match.group(1) if match else "us-east-1"


def _apply_s3_cors(allowed_origins: list[str]) -> None:
    """Apply a generic S3-compatible CORS policy (Minio, AWS S3, Backblaze)."""
    settings = get_settings()
    import boto3
    from botocore.config import Config

    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.BACKBLAZE_ENDPOINT,
            aws_access_key_id=settings.BACKBLAZE_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=settings.BACKBLAZE_SECRET_KEY.get_secret_value(),
            region_name=_derive_region(settings.BACKBLAZE_ENDPOINT),
            config=Config(signature_version="s3v4"),
        )
        client.put_bucket_cors(
            Bucket=settings.S3_BUCKET_NAME,
            CORSConfiguration={"CORSRules": _build_s3_cors(allowed_origins)},
        )
        logger.info("Applied S3 bucket CORS to %s", settings.S3_BUCKET_NAME)

        # Read the rules back so a silently rejected policy does not go unnoticed.
        try:
            rules = (
                client.get_bucket_cors(Bucket=settings.S3_BUCKET_NAME).get("CORSRules")
                or []
            )
        except Exception as e:  # noqa: BLE001 — read-back is best-effort
            logger.warning("Could not read back S3 bucket CORS rules: %s", e)
            return

        recorded: list[str] = []
        for rule in rules:
            for origin in rule.get("AllowedOrigins") or []:
                if origin not in recorded:
                    recorded.append(origin)

        missing = _missing_origins(allowed_origins, recorded)
        if missing:
            logger.error(
                "Bucket %s did not record these CORS origins: %s — browser "
                "uploads from them will fail the preflight with 403.",
                settings.S3_BUCKET_NAME,
                ", ".join(missing),
            )
    except Exception as e:  # noqa: BLE001 — startup must not crash
        logger.error("Failed to apply S3 bucket CORS: %s", e)


if __name__ == "__main__":  # pragma: no cover — manual CLI use
    import sys

    logging.basicConfig(level=logging.INFO)

    if "--check" in sys.argv[1:]:
        missing_origins = missing_bucket_cors_origins()
        if missing_origins is None:
            print("Bucket CORS check unavailable — see logs above.")
            raise SystemExit(2)
        if missing_origins:
            print("MISSING bucket CORS origins: " + ", ".join(missing_origins))
            raise SystemExit(1)
        print("Bucket CORS OK — every expected origin is allowed.")
        raise SystemExit(0)

    apply_bucket_cors()
