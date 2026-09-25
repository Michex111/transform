"""Unit tests for the pure storage-quota arithmetic.

No database, no HTTP: this is the rule both the pre-flight and the finalize
checks call, so the boundary behaviour is pinned here once.
"""

import pytest

from src.domain.subscriptions.policies.storage_quota import (
    StorageQuotaDecision,
    evaluate_storage_quota,
)

GB = 1024 * 1024 * 1024


def test_a_file_that_exactly_fills_the_quota_is_allowed() -> None:
    """The quota is a hard ceiling, not an exclusive bound: used + size == limit
    must pass. Getting this wrong by one byte would reject the single-file case
    the FREE tier is explicitly designed to allow (5 GiB cap, 5 GiB quota)."""
    decision = evaluate_storage_quota(
        limit_bytes=5 * GB, used_bytes=0, file_size=5 * GB,
    )
    assert decision.allowed is True
    assert decision.available_bytes == 5 * GB


def test_one_byte_over_the_quota_is_rejected() -> None:
    decision = evaluate_storage_quota(
        limit_bytes=5 * GB, used_bytes=0, file_size=5 * GB + 1,
    )
    assert decision.allowed is False


def test_usage_is_subtracted_from_the_limit() -> None:
    decision = evaluate_storage_quota(
        limit_bytes=5 * GB, used_bytes=4 * GB, file_size=1 * GB,
    )
    assert decision.used_bytes == 4 * GB
    assert decision.available_bytes == 1 * GB
    assert decision.allowed is True

    one_over = evaluate_storage_quota(
        limit_bytes=5 * GB, used_bytes=4 * GB, file_size=1 * GB + 1,
    )
    assert one_over.allowed is False


def test_available_bytes_is_clamped_at_zero_when_over_quota() -> None:
    """An account already over quota must report 0 available, never a negative
    number — a negative would render as nonsense in the UI and would make
    ``file_size <= available_bytes`` accidentally true for a zero-byte file."""
    decision = evaluate_storage_quota(
        limit_bytes=5 * GB, used_bytes=6 * GB, file_size=0,
    )
    assert decision.available_bytes == 0
    assert decision.allowed is True  # a zero-byte file still "fits" in 0 bytes
    assert decision.available_bytes >= 0


def test_zero_quota_tier_behaves_sanely() -> None:
    """A zero-quota tier: nothing fits, and the numbers stay coherent."""
    assert evaluate_storage_quota(
        limit_bytes=0, used_bytes=0, file_size=0,
    ).allowed is True
    assert evaluate_storage_quota(
        limit_bytes=0, used_bytes=0, file_size=1,
    ).allowed is False
    assert evaluate_storage_quota(
        limit_bytes=0, used_bytes=0, file_size=1,
    ).available_bytes == 0


@pytest.mark.parametrize(
    ("limit", "used", "size"),
    [(-1, 0, 0), (0, -1, 0), (0, 0, -1)],
)
def test_negative_inputs_are_a_caller_bug(limit: int, used: int, size: int) -> None:
    """A negative limit/usage means a mis-read column; clamping it would hide
    the bug behind a passing quota check."""
    with pytest.raises(ValueError):
        evaluate_storage_quota(limit_bytes=limit, used_bytes=used, file_size=size)


def test_decision_is_immutable() -> None:
    decision = evaluate_storage_quota(limit_bytes=10, used_bytes=1, file_size=2)
    assert isinstance(decision, StorageQuotaDecision)
    with pytest.raises(Exception):
        decision.file_size = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Types that must survive the JSON response
# ---------------------------------------------------------------------------


def test_decimal_inputs_are_normalised_to_int() -> None:
    """Regression: PostgreSQL returns ``Decimal`` for ``SUM()`` over a BIGINT.

    The decision's numbers are embedded in the structured 413 body an over-quota
    upload receives, and FastAPI's JSON encoder raises on a ``Decimal`` — so the
    refusal became a **500** instead of "not enough storage". SQLite hands back
    a plain ``int``, so every test in this file passed while the real API was
    broken; this case feeds the values Postgres actually produces.

    Asserting the type (not just equality) is the point: ``Decimal(4) == 4`` is
    true, so an equality-only assertion would pass against the broken code.
    """
    from decimal import Decimal

    decision = evaluate_storage_quota(
        limit_bytes=Decimal("5368709120"),
        used_bytes=Decimal("1048576"),
        file_size=Decimal("5368709120"),
    )

    for value in (decision.limit_bytes, decision.used_bytes, decision.file_size):
        assert type(value) is int, f"expected int, got {type(value).__name__}"
    assert type(decision.available_bytes) is int
    assert decision.allowed is False
    assert decision.available_bytes == 5367660544


def test_a_decimal_decision_is_json_serialisable() -> None:
    """The exact assertion the 500 would have failed.

    Reproduces the response body construction that exploded: ``json.dumps`` of a
    ``Decimal`` raises ``TypeError``. Encoding the whole decision body here is
    what turns "wrong type" into "the API 500s".
    """
    import json
    from decimal import Decimal

    decision = evaluate_storage_quota(
        limit_bytes=Decimal("5368709120"),
        used_bytes=Decimal("1048576"),
        file_size=Decimal("5368709120"),
    )
    body = {
        "code": "STORAGE_QUOTA_EXCEEDED",
        "limit_bytes": decision.limit_bytes,
        "used_bytes": decision.used_bytes,
        "available_bytes": decision.available_bytes,
        "file_size": decision.file_size,
    }

    # No default= handler: a Decimal here must raise, which is the bug.
    encoded = json.dumps(body)

    assert '"used_bytes": 1048576' in encoded
    assert "Decimal" not in encoded

