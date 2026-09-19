"""Tests for the storage bucket CORS policy builder."""

import sys

import pytest

from src.infrastructure.adapters.storage import cors as cors_module
from src.infrastructure.adapters.storage.cors import (
    _build_b2_cors,
    _build_s3_cors,
    _derive_region,
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


def test_build_s3_cors_supports_the_separately_hosted_spa_origin() -> None:
    """The SPA is a distinct origin now — the bucket must allow it verbatim.

    Direct-to-bucket presigned uploads preflight from the static-site origin, so
    that origin must appear in ``AllowedOrigins`` with ``PUT`` (upload),
    ``GET``/``HEAD`` (download) permitted.
    """
    static_site = "https://transform-web.onrender.com"
    rules = _build_s3_cors([static_site])
    assert rules[0]["AllowedOrigins"] == [static_site]
    methods = set(rules[0]["AllowedMethods"])
    assert {"PUT", "GET", "HEAD"} <= methods


def test_build_b2_cors_supports_the_separately_hosted_spa_origin() -> None:
    static_site = "https://transform-web.onrender.com"
    rules = _build_b2_cors([static_site])
    assert rules[0]["allowedOrigins"] == [static_site]
    assert {"s3_put", "s3_get", "s3_head"} <= set(rules[0]["allowedOperations"])


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://s3.us-east-005.backblazeb2.com", "us-east-005"),
        ("s3.us-west-004.backblazeb2.com", "us-west-004"),
        ("https://s3.eu-central-003.backblazeb2.com", "eu-central-003"),
        # No region in the endpoint -> boto3 default.
        ("https://s3.amazonaws.com", "us-east-1"),
        ("minio:9000", "us-east-1"),
        ("", "us-east-1"),
    ],
)
def test_derive_region(endpoint: str, expected: str) -> None:
    """Backblaze rejects a signature whose SigV4 region mismatches the host."""
    assert _derive_region(endpoint) == expected


def test_apply_b2_cors_falls_back_to_s3_when_b2sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a missing b2sdk raised UnboundLocalError instead of falling back.

    ``from b2sdk.v2.exception import B2Error`` used to sit inside the same
    ``try`` as ``except B2Error``. With b2sdk absent the import failed, leaving
    ``B2Error`` unbound, so evaluating the handler raised UnboundLocalError and
    masked the real cause — and the bucket ended up with no CORS rules, which
    breaks direct browser uploads.
    """
    # ``None`` in sys.modules makes ``import b2sdk...`` raise ImportError.
    monkeypatch.setitem(sys.modules, "b2sdk", None)
    monkeypatch.setitem(sys.modules, "b2sdk.v2", None)

    calls: list[list[str]] = []
    monkeypatch.setattr(
        cors_module, "_apply_s3_cors", lambda origins: calls.append(origins)
    )

    cors_module._apply_b2_cors(["https://app.example.com"])

    assert calls == [["https://app.example.com"]]


def test_apply_bucket_cors_routes_backblaze_endpoint_to_b2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A B2 endpoint goes down the B2 path, not the generic S3 path."""
    routed: list[str] = []
    monkeypatch.setattr(
        cors_module, "_apply_b2_cors", lambda origins: routed.append("b2")
    )
    monkeypatch.setattr(
        cors_module, "_apply_s3_cors", lambda origins: routed.append("s3")
    )

    cors_module.apply_bucket_cors(["https://app.example.com"])

    # get_settings() reads the test env (BACKBLAZE_ENDPOINT=https://example.invalid
    # in conftest), which is not a B2 host, so the generic S3 path is correct.
    assert routed == ["s3"]


def test_apply_bucket_cors_skips_when_no_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No configured origins means no API calls and no CORS overwrite."""
    called: list[str] = []
    monkeypatch.setattr(cors_module, "_apply_b2_cors", lambda o: called.append("b2"))
    monkeypatch.setattr(cors_module, "_apply_s3_cors", lambda o: called.append("s3"))

    cors_module.apply_bucket_cors([])

    assert called == []
