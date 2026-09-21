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
