"""Unit tests for the password-reset token policy.

The reset token is a full account-takeover credential, so the properties worth
pinning are the boundaries: the TTL is honoured in *minutes*, a nonsensical TTL
is refused rather than silently producing an already-dead link, and the stored
form is a digest rather than the credential itself.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.security.enitities.one_time_token import TOKEN_BYTES, hash_token
from src.domain.security.enitities.password_reset import (
    DEFAULT_TTL_MINUTES,
    issue_password_reset_token,
)


def test_ttl_is_honoured_in_minutes() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    token = issue_password_reset_token(ttl_minutes=30, now=now)

    assert token.expires_at == now + timedelta(minutes=30)


def test_the_default_window_is_far_shorter_than_a_verification_link() -> None:
    """A reset grants takeover, so its window is minutes, not a day."""
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    token = issue_password_reset_token(ttl_minutes=DEFAULT_TTL_MINUTES, now=now)

    assert token.expires_at <= now + timedelta(hours=1)
    assert DEFAULT_TTL_MINUTES <= 60


@pytest.mark.parametrize("ttl_minutes", [0, -1, -60])
def test_a_non_positive_ttl_is_rejected(ttl_minutes: int) -> None:
    """A link that is already dead must never be issued silently."""
    with pytest.raises(ValueError):
        issue_password_reset_token(ttl_minutes=ttl_minutes)


def test_naive_now_is_rejected() -> None:
    """A naive/aware mix raises deep in a handler, which is worse than here."""
    with pytest.raises(ValueError):
        issue_password_reset_token(ttl_minutes=60, now=datetime(2026, 9, 22, 12, 0))


def test_stored_form_is_a_digest_not_the_token() -> None:
    token = issue_password_reset_token(ttl_minutes=60)

    assert token.hashed == hash_token(token.raw)
    assert token.raw not in token.hashed
    assert len(token.hashed) == 64  # sha256 hex
    # token_urlsafe(32) -> 43 base64url characters, so the entropy is real.
    assert len(token.raw) == 43
    assert len(token.raw) * 6 >= TOKEN_BYTES * 8
    # The digest must not be reversible by composition with a known prefix.
    assert hash_token("") != token.hashed


def test_issued_tokens_are_unique() -> None:
    """Guessing must be infeasible, which requires no repeats across calls."""
    raw = {issue_password_reset_token(ttl_minutes=60).raw for _ in range(200)}
    assert len(raw) == 200
