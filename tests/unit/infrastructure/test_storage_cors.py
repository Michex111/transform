"""Tests for the storage bucket CORS policy builder."""

import logging
import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from src.infrastructure.adapters.storage import cors as cors_module
from src.infrastructure.config.settings import get_settings
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

    # conftest pins BACKBLAZE_ENDPOINT to ``https://example.invalid``, which is
    # not a B2 host — with that value this test would exercise the S3 *fallback*
    # and assert the opposite of its name. Point the endpoint at a real B2 host
    # for the duration of the test and drop the cached Settings so the routing
    # decision under test is genuinely the B2 branch.
    monkeypatch.setenv("BACKBLAZE_ENDPOINT", "s3.us-east-005.backblazeb2.com")
    get_settings.cache_clear()
    try:
        cors_module.apply_bucket_cors(["https://app.example.com"])
    finally:
        # Leave no cached Settings behind for the next test (the env var itself
        # is restored by monkeypatch).
        get_settings.cache_clear()

    assert routed == ["b2"]


def test_apply_bucket_cors_falls_back_to_s3_for_a_non_b2_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-B2 (S3-compatible/MinIO) endpoint takes the generic S3 path."""
    routed: list[str] = []
    monkeypatch.setattr(
        cors_module, "_apply_b2_cors", lambda origins: routed.append("b2")
    )
    monkeypatch.setattr(
        cors_module, "_apply_s3_cors", lambda origins: routed.append("s3")
    )

    # conftest's default endpoint is exactly this case, but pin it explicitly so
    # the fallback stays covered independently of the test environment.
    monkeypatch.setenv("BACKBLAZE_ENDPOINT", "https://example.invalid")
    get_settings.cache_clear()
    try:
        cors_module.apply_bucket_cors(["https://app.example.com"])
    finally:
        get_settings.cache_clear()

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


# ---------------------------------------------------------------------------
# Shared-bucket origin pinning + read-back verification
#
# Production uploads broke because bucket CORS is BUCKET-GLOBAL: a local dev
# process rewrote the single rule set from its own localhost-only config and
# silently deleted the deployed SPA's origin. Backblaze then answered the
# browser's preflight with a bare 403 (no CORS headers) and the SPA reported a
# generic object-storage error. The production container could not repair it
# either, because ``b2sdk`` was not installed and Backblaze rejects the S3
# PutBucketCors fallback once native rules exist.
# ---------------------------------------------------------------------------


def _settings_namespace(**overrides: object) -> SimpleNamespace:
    """A minimal stand-in for Settings covering what cors.py reads."""
    base: dict[str, object] = {
        "BACKBLAZE_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
        "BACKBLAZE_ACCESS_KEY": SecretStr("key"),
        "BACKBLAZE_SECRET_KEY": SecretStr("secret"),
        "S3_BUCKET_NAME": "test-bucket",
        "ALLOWED_ORIGINS": [],
        "S3_CORS_ALLOWED_ORIGINS": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_normalize_origins_trims_dedupes_and_drops_blanks() -> None:
    assert cors_module._normalize_origins(
        ["  http://a.test ", "http://a.test", "", None, "http://b.test"]
    ) == ["http://a.test", "http://b.test"]


def test_configured_origins_merges_both_settings_and_pinned_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ALLOWED_ORIGINS`` and ``S3_CORS_ALLOWED_ORIGINS`` drift; both are needed."""
    monkeypatch.setattr(
        cors_module,
        "get_settings",
        lambda: _settings_namespace(
            S3_CORS_ALLOWED_ORIGINS=["https://spa.test"],
            ALLOWED_ORIGINS=["https://spa.test", "https://api.test"],
        ),
    )

    origins = cors_module._configured_origins()

    assert "https://spa.test" in origins
    assert "https://api.test" in origins
    for pinned in cors_module.ALWAYS_ALLOWED_ORIGINS:
        assert pinned in origins


def test_apply_bucket_cors_keeps_production_origin_alongside_local_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a dev config listing only localhost must not drop the SPA origin."""
    monkeypatch.setattr(
        cors_module,
        "get_settings",
        lambda: _settings_namespace(
            S3_CORS_ALLOWED_ORIGINS=["http://localhost:5173"],
            ALLOWED_ORIGINS=["http://localhost:5173"],
        ),
    )
    written: list[list[str]] = []
    monkeypatch.setattr(cors_module, "_apply_b2_cors", lambda origins: written.append(origins))

    cors_module.apply_bucket_cors()

    assert len(written) == 1
    assert written[0] == [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:8000",
        "https://transform-web.onrender.com",
    ]


def test_origins_from_b2_rules_flattens_and_dedupes() -> None:
    assert cors_module._origins_from_b2_rules(
        [
            {"allowedOrigins": ["https://a.test", "https://b.test"]},
            {"allowedOrigins": ["https://b.test"]},
            {"allowedOrigins": []},
            {},
        ]
    ) == ["https://a.test", "https://b.test"]


def test_missing_origins_treats_unreadable_rules_as_missing() -> None:
    """An unverifiable policy must never be reported as OK."""
    assert cors_module._missing_origins(["https://a.test"], None) == ["https://a.test"]


def test_missing_origins_reports_only_absent_origins() -> None:
    assert cors_module._missing_origins(
        ["https://a.test", "https://b.test"], ["https://a.test"]
    ) == ["https://b.test"]


def test_apply_b2_cors_logs_error_when_b2sdk_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The fallback is unreliable on B2, so the log must be an actionable ERROR."""
    monkeypatch.setitem(sys.modules, "b2sdk", None)
    monkeypatch.setitem(sys.modules, "b2sdk.v2", None)
    monkeypatch.setattr(cors_module, "_apply_s3_cors", lambda origins: None)

    with caplog.at_level(logging.ERROR, logger=cors_module.__name__):
        cors_module._apply_b2_cors(["https://app.example.com"])

    assert "b2sdk is not installed" in caplog.text


def test_apply_b2_cors_logs_error_when_bucket_did_not_record_origin(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A dropped origin is invisible to the browser until uploads fail — report it."""
    _install_fake_b2sdk(monkeypatch, recorded_origins=["http://localhost:5173"])
    monkeypatch.setattr(cors_module, "get_settings", lambda: _settings_namespace())

    with caplog.at_level(logging.ERROR, logger=cors_module.__name__):
        cors_module._apply_b2_cors(["https://transform-web.onrender.com"])

    assert "did not record" in caplog.text
    assert "https://transform-web.onrender.com" in caplog.text


def test_apply_b2_cors_logs_verification_when_all_origins_recorded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_fake_b2sdk(monkeypatch)
    monkeypatch.setattr(cors_module, "get_settings", lambda: _settings_namespace())

    with caplog.at_level(logging.INFO, logger=cors_module.__name__):
        cors_module._apply_b2_cors(["https://transform-web.onrender.com"])

    assert "Verified 1 bucket CORS origin(s)" in caplog.text


def _install_fake_b2sdk(
    monkeypatch: pytest.MonkeyPatch, *, recorded_origins: list[str] | None = None
) -> None:
    """Install a minimal fake ``b2sdk`` into ``sys.modules``.

    ``recorded_origins`` overrides what the bucket reports back, simulating a
    rule the service silently dropped (``None`` echoes what was written).
    """
    state: dict[str, list[dict]] = {"rules": []}

    class FakeBucket:
        type_ = "allPrivate"

        def update(
            self,
            bucket_type: str | None = None,
            cors_rules: list[dict[str, list[str]]] | None = None,
        ) -> None:
            if recorded_origins is not None:
                origins = recorded_origins
            else:
                assert cors_rules is not None  # always passed by _apply_b2_cors
                origins = cors_rules[0]["allowedOrigins"]
            state["rules"] = [{"allowedOrigins": list(origins)}]

        def as_dict(self) -> dict:
            return {"corsRules": state["rules"]}

    class FakeB2Api:
        def __init__(self, _info) -> None:
            self.bucket = FakeBucket()

        def authorize_account(self, *_args, **_kwargs) -> None:
            return None

        def get_bucket_by_name(self, _name: str) -> FakeBucket:
            return self.bucket

    v2 = types.ModuleType("b2sdk.v2")
    v2.AbstractAccountInfo = object  # type: ignore[attr-defined]
    v2.InMemoryAccountInfo = lambda: object()  # type: ignore[attr-defined]
    v2.B2Api = FakeB2Api  # type: ignore[attr-defined]

    exception = types.ModuleType("b2sdk.v2.exception")

    class B2Error(Exception):
        pass

    exception.B2Error = B2Error  # type: ignore[attr-defined]

    b2sdk = types.ModuleType("b2sdk")
    b2sdk.v2 = v2  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "b2sdk", b2sdk)
    monkeypatch.setitem(sys.modules, "b2sdk.v2", v2)
    monkeypatch.setitem(sys.modules, "b2sdk.v2.exception", exception)
