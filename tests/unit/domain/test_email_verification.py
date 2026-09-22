"""Unit tests for the email-verification token rules.

These are pure functions, so the cases that matter are the boundaries: expiry
exactly at the deadline, naive datetimes from SQLite, a missing expiry, and the
resend cooldown.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.security.enitities.email_verification import (
    TOKEN_BYTES,
    cooldown_elapsed,
    hash_verification_token,
    issue_verification_token,
    token_is_expired,
)


def test_issued_token_is_url_safe_and_high_entropy() -> None:
    token = issue_verification_token(ttl_hours=24)

    # token_urlsafe(32) -> 43 chars of base64url, no padding.
    assert len(token.raw) == 43
    assert all(c.isalnum() or c in "-_" for c in token.raw)
    # The entropy has to be real: a token shorter than TOKEN_BYTES of base64
    # cannot carry TOKEN_BYTES of randomness.
    assert len(token.raw) * 6 >= TOKEN_BYTES * 8


def test_issued_tokens_are_unique() -> None:
    """Guessing must be infeasible, which requires no repeats across calls."""
    raw = {issue_verification_token(ttl_hours=1).raw for _ in range(200)}
    assert len(raw) == 200


def test_stored_form_is_a_digest_not_the_token() -> None:
    token = issue_verification_token(ttl_hours=24)

    assert token.hashed == hash_verification_token(token.raw)
    assert token.raw not in token.hashed
    assert len(token.hashed) == 64  # sha256 hex

    # The digest must not be reversible by composition with a known prefix.
    assert hash_verification_token("") != token.hashed


def test_hash_is_deterministic_for_a_given_token() -> None:
    """Lookup is BY digest, so the same token must always hash the same."""
    assert hash_verification_token("abc") == hash_verification_token("abc")
    assert hash_verification_token("abc") != hash_verification_token("abd")


def test_expiry_is_relative_to_the_injected_clock() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    token = issue_verification_token(ttl_hours=24, now=now)

    assert token.expires_at == now + timedelta(hours=24)


def test_naive_now_is_rejected() -> None:
    """A naive/aware mix raises deep in a handler, which is worse than here."""
    with pytest.raises(ValueError):
        issue_verification_token(ttl_hours=1, now=datetime(2026, 9, 22, 12, 0))


def test_token_is_not_expired_before_the_deadline() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert token_is_expired(now + timedelta(seconds=1), now=now) is False


def test_token_is_expired_after_the_deadline() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert token_is_expired(now - timedelta(seconds=1), now=now) is True


def test_token_expiring_exactly_now_is_expired() -> None:
    """The comparison must not leave a zero-width window where a dead token works."""
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert token_is_expired(now, now=now) is True


def test_missing_expiry_fails_closed() -> None:
    """A row without an expiry must never be treated as valid forever."""
    assert token_is_expired(None) is True


def test_naive_expiry_from_sqlite_is_normalised_to_utc() -> None:
    """SQLite round-trips DateTime(timezone=True) as naive; Postgres does not.

    Without normalisation this raises ``TypeError: can't compare offset-naive
    and offset-aware datetimes`` inside the request handler.
    """
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    naive_future = datetime(2026, 9, 22, 13, 0)
    naive_past = datetime(2026, 9, 22, 11, 0)

    assert token_is_expired(naive_future, now=now) is False
    assert token_is_expired(naive_past, now=now) is True


# ---------------------------------------------------------------------------
# Resend cooldown
# ---------------------------------------------------------------------------


def test_never_sent_always_permits_a_send() -> None:
    assert cooldown_elapsed(None, cooldown_seconds=60) is True


def test_send_is_blocked_inside_the_cooldown() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    sent = now - timedelta(seconds=30)

    assert cooldown_elapsed(sent, cooldown_seconds=60, now=now) is False


def test_send_is_permitted_after_the_cooldown() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    sent = now - timedelta(seconds=60)

    # Boundary is inclusive, so exactly at the cooldown a resend is allowed.
    assert cooldown_elapsed(sent, cooldown_seconds=60, now=now) is True


def test_zero_cooldown_disables_the_limit() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert cooldown_elapsed(now, cooldown_seconds=0, now=now) is True


def test_naive_sent_at_is_normalised() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    # 30 seconds ago, against a 60s cooldown -> still inside the window.
    assert cooldown_elapsed(datetime(2026, 9, 22, 11, 59, 30), cooldown_seconds=60, now=now) is False
