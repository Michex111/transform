"""Tests for production-safety settings validation."""

from pathlib import Path

import pytest

from src.infrastructure.config.settings import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
STATIC_SITE_ORIGIN = "https://transform-web.onrender.com"


def _settings(**overrides) -> Settings:
    base = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "s" * 40,
        "REDIS_URL": "redis://localhost:6379/0",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "BACKBLAZE_ENDPOINT": "s3.amazonaws.com",
        "BACKBLAZE_ACCESS_KEY": "ak",
        "BACKBLAZE_SECRET_KEY": "sk",
        "BASE_TARGET_KEY": "output/",
        "BACKBLAZE_USE_SSL": True,
        "ALLOWED_ORIGINS": ["https://app.example.com"],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


def test_production_with_secure_config_validates() -> None:
    _settings().validate()  # should not raise


def test_production_rejects_weak_secret_key() -> None:
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _settings(SECRET_KEY="change-me-to-a-random-secret-key").validate()
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _settings(SECRET_KEY="short").validate()


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        _settings(ALLOWED_ORIGINS=["*"]).validate()


def test_frontend_dist_dir_defaults_to_none() -> None:
    """The API no longer serves the SPA — the retained field is a None no-op.

    It is kept only so an existing deployment that still sets
    ``FRONTEND_DIST_DIR`` in its environment does not crash the boot
    (``Settings`` uses ``extra="forbid"``). Nothing reads it.
    """
    assert _settings().FRONTEND_DIST_DIR is None


@pytest.mark.parametrize("key", ["ALLOWED_ORIGINS", "S3_CORS_ALLOWED_ORIGINS"])
def test_env_example_allows_the_separately_hosted_spa_origin(key: str) -> None:
    """The SPA is a distinct origin; the env template must allow it everywhere.

    Guards against a cutover regression where the API rejects the static site's
    origin (CORS) or the bucket rejects its direct presigned uploads.
    """
    line = next(
        (ln for ln in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines() if ln.startswith(f"{key}=")),
        None,
    )
    assert line is not None, f"{key} missing from .env.example"
    assert STATIC_SITE_ORIGIN in line, f"{STATIC_SITE_ORIGIN} not allowed by {key} in .env.example"


def test_production_rejects_plaintext_object_storage() -> None:
    with pytest.raises(RuntimeError, match="BACKBLAZE_USE_SSL"):
        _settings(BACKBLAZE_USE_SSL=False).validate()


def test_development_allows_weak_secret() -> None:
    dev = _settings(ENVIRONMENT="development", SECRET_KEY="change-me-to-a-random-secret-key")
    dev.validate()  # should not raise


def test_unknown_environment_fails_closed() -> None:
    """SEC-8: a typo like 'prod' must not silently skip the production checks."""
    with pytest.raises(RuntimeError, match="Unsupported ENVIRONMENT"):
        _settings(ENVIRONMENT="prod", SECRET_KEY="change-me-to-a-random-secret-key").validate()


# ---------------------------------------------------------------------------
# Email transport configuration
# ---------------------------------------------------------------------------


def test_email_backend_defaults_to_auto_and_resolves_to_console() -> None:
    """With no credentials, ``auto`` must degrade to the log-only transport."""
    settings = _settings(ENVIRONMENT="development")

    assert settings.EMAIL_BACKEND == "auto"
    assert settings._resolve_email_backend() == "console"


def test_auto_prefers_resend_when_an_api_key_is_present() -> None:
    assert _settings(RESEND_API_KEY="re_123")._resolve_email_backend() == "resend"


def test_auto_prefers_resend_over_smtp() -> None:
    """Resend wins because an API key is a deliberate, single-purpose credential."""
    settings = _settings(RESEND_API_KEY="re_123", SMTP_HOST="smtp.example.com")

    assert settings._resolve_email_backend() == "resend"


def test_auto_falls_back_to_smtp_when_only_smtp_is_configured() -> None:
    assert _settings(SMTP_HOST="smtp.example.com")._resolve_email_backend() == "smtp"


def test_explicit_resend_without_a_key_is_a_startup_error() -> None:
    """An explicit choice must never be silently downgraded to the log sink."""
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        _settings(EMAIL_BACKEND="resend").validate()


def test_explicit_smtp_without_a_host_is_a_startup_error() -> None:
    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        _settings(EMAIL_BACKEND="smtp").validate()


def test_unknown_email_backend_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="Unsupported EMAIL_BACKEND"):
        _settings(EMAIL_BACKEND="sendgrid").validate()


def test_ssl_and_starttls_together_are_rejected() -> None:
    """Both at once means "connect with TLS, then upgrade to TLS"."""
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        _settings(SMTP_USE_SSL=True, SMTP_USE_STARTTLS=True).validate()


def test_non_positive_verification_ttl_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="EMAIL_VERIFICATION_TTL_HOURS"):
        _settings(EMAIL_VERIFICATION_TTL_HOURS=0).validate()


def test_production_does_not_require_an_email_transport() -> None:
    """A hard failure here would turn "credentials not added yet" into an outage.

    The degraded state is instead reported by `_warn_on_degraded_email_delivery`,
    and the sign-in gate stays open (see
    `email_verification_is_enforced`) so new sign-ups are not locked out of an
    inbox that can never receive the link.
    """
    _settings().validate()  # should not raise


def test_console_transport_in_production_logs_an_error(caplog) -> None:
    """The operator must be able to see this in the log stream without guessing."""
    import logging

    with caplog.at_level(logging.ERROR):
        _settings(EMAIL_VERIFICATION_REQUIRED=True).validate()

    assert any(
        "email verification is SUSPENDED" in record.message.lower()
        or "SUSPENDED" in record.message
        for record in caplog.records
    )
    assert any("RESEND_API_KEY" in record.getMessage() for record in caplog.records)


def test_localhost_app_base_url_in_production_logs_an_error(caplog) -> None:
    """Verification links pointing at the developer's machine are unusable."""
    import logging

    with caplog.at_level(logging.ERROR):
        _settings(RESEND_API_KEY="re_123", APP_BASE_URL="http://localhost:5173").validate()

    assert any("APP_BASE_URL" in record.getMessage() for record in caplog.records)


def test_configured_transport_logs_no_degraded_error(caplog) -> None:
    import logging

    with caplog.at_level(logging.ERROR):
        _settings(
            RESEND_API_KEY="re_123",
            APP_BASE_URL="https://transform-web.onrender.com",
        ).validate()

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_env_example_documents_the_email_transport_settings() -> None:
    """Guards against a setting that exists only in code and never gets deployed."""
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")

    for key in (
        "EMAIL_BACKEND=",
        "EMAIL_FROM_ADDRESS=",
        "RESEND_API_KEY=",
        "SMTP_HOST=",
        "APP_BASE_URL=",
        "EMAIL_VERIFICATION_REQUIRED=",
    ):
        assert key in text, f"{key} missing from .env.example"


# ---------------------------------------------------------------------------
# Upload size limits / multipart configuration
# ---------------------------------------------------------------------------

def test_env_example_documents_the_upload_limit_settings() -> None:
    """Same guard as the email settings: a limit that only exists in code is a
    limit no deployment can ever tune (or discover)."""
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")

    for key in (
        "MAX_UPLOAD_FILE_SIZE_BYTES=",
        "GUEST_MAX_FILE_SIZE=",
        "FREE_MAX_FILE_SIZE=",
        "PRO_MAX_FILE_SIZE=",
        "PRO_PLUS_MAX_FILE_SIZE=",
        "ENTERPRISE_MAX_FILE_SIZE=",
        "MULTIPART_THRESHOLD_BYTES=",
        "MULTIPART_PART_SIZE_BYTES=",
        "LARGE_UPLOAD_URL_TTL_MINUTES=",
        "UPLOAD_URL_TTL_MINUTES=",
    ):
        assert key in text, f"{key} missing from .env.example"


def test_default_ceiling_is_five_gib_and_authenticated_tiers_reach_it() -> None:
    """5 GiB is the requested per-file limit and the single-PUT provider cap."""
    settings = _settings()

    assert settings.MAX_UPLOAD_FILE_SIZE_BYTES == 5 * 1024**3
    assert settings.FREE_MAX_FILE_SIZE == 5 * 1024**3
    assert settings.PRO_MAX_FILE_SIZE == 5 * 1024**3
    assert settings.PRO_PLUS_MAX_FILE_SIZE == 5 * 1024**3


def test_enterprise_has_its_own_cap_not_an_alias_of_pro_plus() -> None:
    """ENTERPRISE used to be hard-wired to the PRO_PLUS value, which made an
    enterprise-specific override impossible to express. It must be its own
    setting and it must actually be read."""
    assert _settings(ENTERPRISE_MAX_FILE_SIZE=1024).ENTERPRISE_MAX_FILE_SIZE == 1024


def test_guest_ceiling_stays_small() -> None:
    """Guests are unauthenticated: raising their ceiling would hand anonymous
    callers a large free storage/bandwidth sink."""
    assert _settings().GUEST_MAX_FILE_SIZE == 50 * 1024 * 1024


@pytest.mark.parametrize(
    "key",
    [
        "GUEST_MAX_FILE_SIZE",
        "FREE_MAX_FILE_SIZE",
        "PRO_MAX_FILE_SIZE",
        "PRO_PLUS_MAX_FILE_SIZE",
        "ENTERPRISE_MAX_FILE_SIZE",
    ],
)
def test_a_tier_cap_above_the_ceiling_is_rejected(key: str) -> None:
    """The invariant that stops a tier advertising a file the store cannot take."""
    with pytest.raises(RuntimeError, match=key):
        _settings(**{key: 5 * 1024**3 + 1}).validate()


@pytest.mark.parametrize(
    "key",
    [
        "MAX_UPLOAD_FILE_SIZE_BYTES",
        "MULTIPART_THRESHOLD_BYTES",
        "MULTIPART_PART_SIZE_BYTES",
        "LARGE_UPLOAD_URL_TTL_MINUTES",
        "UPLOAD_URL_TTL_MINUTES",
    ],
)
def test_non_positive_upload_values_are_rejected(key: str) -> None:
    with pytest.raises(RuntimeError, match=key):
        _settings(**{key: 0}).validate()


def test_threshold_above_the_ceiling_is_rejected() -> None:
    """Otherwise a file between the ceiling and the threshold would be neither
    accepted whole nor routed to multipart — a dead zone."""
    with pytest.raises(RuntimeError, match="MULTIPART_THRESHOLD_BYTES"):
        _settings(MULTIPART_THRESHOLD_BYTES=5 * 1024**3 + 1).validate()


def test_part_size_keeping_the_ceiling_under_ten_thousand_parts_is_accepted() -> None:
    """The default configuration must clear S3's 10 000-part limit with room to
    spare: ceil(5 GiB / 64 MiB) = 80 parts."""
    settings = _settings()

    assert settings.MULTIPART_PART_SIZE_BYTES == 64 * 1024 * 1024
    assert -(-settings.MAX_UPLOAD_FILE_SIZE_BYTES // settings.MULTIPART_PART_SIZE_BYTES) == 80
    settings.validate()  # must not raise


def test_part_size_too_small_for_the_ceiling_is_rejected() -> None:
    """A part size that needs more than 10 000 parts would fail only at the END
    of a maximal transfer, so it is rejected at boot instead."""
    with pytest.raises(RuntimeError, match="MULTIPART_PART_SIZE_BYTES"):
        _settings(MULTIPART_PART_SIZE_BYTES=1024).validate()


def test_part_size_at_exactly_the_required_boundary_is_accepted() -> None:
    """ceil(ceiling / 10 000) parts is exactly at the limit, which is allowed."""
    required = -(-5 * 1024**3 // 10_000)
    _settings(MULTIPART_PART_SIZE_BYTES=required).validate()  # must not raise

