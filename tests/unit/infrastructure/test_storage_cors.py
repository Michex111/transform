"""Tests for the storage bucket CORS policy builder."""

from src.infrastructure.adapters.storage.cors import (
    _build_b2_cors,
    _build_s3_cors,
    _is_backblaze,
)


def test_is_backblaze_detects_b2_endpoint() -> None:
    assert _is_backblaze("https://s3.us-east-005.backblazeb2.com") is True
    assert _is_backblaze("https://s3.amazonaws.com") is False
    assert _is_backblaze("minio:9000") is False


def test_build_b2_cors_uses_exact_origins_and_required_name() -> None:
    rules = _build_b2_cors(["http://localhost:5173", "http://localhost:8000"])
    assert len(rules) == 1
    rule = rules[0]
    # B2 rejects wildcards after the hostname, so exact origins are preserved.
    assert rule["allowedOrigins"] == ["http://localhost:5173", "http://localhost:8000"]
    assert rule["corsRuleName"] == "transform-spa-upload"
    assert "s3_put" in rule["allowedOperations"]
    assert "s3_get" in rule["allowedOperations"]
    assert rule["maxAgeSeconds"] == 3600


def test_build_s3_cors_allows_methods_and_origins() -> None:
    rules = _build_s3_cors(["http://localhost:5173"])
    assert len(rules) == 1
    assert rules[0]["AllowedOrigins"] == ["http://localhost:5173"]
    assert set(rules[0]["AllowedMethods"]) == {"GET", "PUT", "POST", "HEAD"}
