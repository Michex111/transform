"""Tests for production-safety settings validation."""

import pytest

from src.infrastructure.config.settings import Settings


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


def test_production_rejects_plaintext_object_storage() -> None:
    with pytest.raises(RuntimeError, match="BACKBLAZE_USE_SSL"):
        _settings(BACKBLAZE_USE_SSL=False).validate()


def test_development_allows_weak_secret() -> None:
    dev = _settings(ENVIRONMENT="development", SECRET_KEY="change-me-to-a-random-secret-key")
    dev.validate()  # should not raise
