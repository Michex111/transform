"""Tests for S3 endpoint normalization."""

from src.infrastructure.adapters.storage.endpoint import normalize_endpoint


def test_bare_host_passthrough() -> None:
    assert normalize_endpoint("minio:9000") == "minio:9000"
    assert normalize_endpoint("localhost:9000") == "localhost:9000"
    assert normalize_endpoint("s3.amazonaws.com") == "s3.amazonaws.com"


def test_scheme_stripped() -> None:
    assert normalize_endpoint("http://minio:9000") == "minio:9000"
    assert normalize_endpoint("https://s3.amazonaws.com") == "s3.amazonaws.com"
    assert normalize_endpoint("http://localhost:9000") == "localhost:9000"
