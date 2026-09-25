"""Unit tests for the phone-verification code rules.

These are pure functions, so the cases that matter are the boundaries: a code
whose deadline is exactly now, a cooldown that has exactly elapsed, leading
zeros in the code, and — most importantly — that a digest computed for one
account does not match for another.
"""

from datetime import UTC, datetime, timedelta

import hashlib

import pytest

from src.domain.security.enitities import phone_verification
from src.domain.security.enitities.phone_verification import (
    MAX_VERIFICATION_ATTEMPTS,
    PHONE_CODE_DIGITS,
    attempts_exhausted,
    code_is_expired,
    code_matches,
    cooldown_elapsed,
    generate_verification_code,
    hash_verification_code,
    is_e164,
    issue_phone_code,
    normalize_phone_number,
    seconds_until_resend_allowed,
)
from src.domain.security.exceptions import InvalidPhoneNumber

SECRET = "test-secret-key-that-is-long-enough-to-be-plausible"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+14155552671", "+14155552671"),
        # The formatting humans actually type.
        ("+1 (415) 555-2671", "+14155552671"),
        ("+44 20 7946 0958", "+442079460958"),
        ("+1.415.555.2671", "+14155552671"),
        ("  +1 415 555 2671  ", "+14155552671"),
        # International dialling prefix, which is why 00 -> + runs after the
        # separators are removed (this input has a space after "00").
        ("0044 20 7946 0958", "+442079460958"),
        ("+91 98765 43210", "+919876543210"),
    ],
)
def test_normalisation_accepts_and_canonicalises(raw: str, expected: str) -> None:
    assert normalize_phone_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "+1234",  # too short (5 digits)
        "+1234567",  # one digit short of the minimum
        "14155552671",  # missing the + (a leading 1 is not a dialling prefix)
        "+1415ABC2671",  # letters
        "+1 415 555 267X",  # letters mixed with separators
        "+1415555267100000",  # too long
        "+04155552671",  # leading zero after the +
        "+",  # nothing at all
        "",  # empty
        "call me maybe",
    ],
)
def test_normalisation_rejects(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone_number(raw)


def test_normalisation_rejects_none() -> None:
    """A missing body field must be a domain error, not an AttributeError."""
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone_number(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+12345678", True),  # 8 digits: the shortest valid E.164 number
        ("+123456789012345", True),  # 15 digits: the longest
        ("+1234567", False),  # 7 digits: one short
        ("+1234567890123456", False),  # 16 digits: one too many
        ("12345678", False),  # no +
        ("+02345678", False),  # country code cannot start with 0
        ("+1 (234) 5678", False),  # must already be normalised
        ("", False),
    ],
)
def test_is_e164_boundaries(value: str, expected: bool) -> None:
    assert is_e164(value) is expected


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def test_generated_code_is_always_six_digits() -> None:
    for _ in range(200):
        code = generate_verification_code()
        assert len(code) == PHONE_CODE_DIGITS
        assert code.isdigit()


def test_generated_code_is_zero_padded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small draw must not produce a short code.

    ``str(randbelow(...))`` would turn 123 into "123" — a different code *and*
    one the user cannot type, because the SMS shows six digits. The padding is
    therefore load-bearing, not cosmetic.
    """
    monkeypatch.setattr(phone_verification.secrets, "randbelow", lambda _n: 123)

    assert generate_verification_code() == "000123"


def test_generated_code_is_the_full_random_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phone_verification.secrets, "randbelow", lambda _n: 999_999)

    assert generate_verification_code() == "999999"


def test_generated_codes_are_not_all_identical() -> None:
    """A degenerate generator (e.g. a fixed default) must fail loudly."""
    assert len({generate_verification_code() for _ in range(50)}) > 1


# ---------------------------------------------------------------------------
# Hashing / matching
# ---------------------------------------------------------------------------


def test_hash_is_deterministic_and_hex_sha256() -> None:
    """Lookup happens by digest, so the same inputs must always hash the same."""
    first = hash_verification_code("123456", secret=SECRET, user_id=7)

    assert first == hash_verification_code("123456", secret=SECRET, user_id=7)
    assert first != hash_verification_code("123457", secret=SECRET, user_id=7)
    assert first != hash_verification_code("123456", secret=SECRET, user_id=8)
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_hash_is_keyed_and_bound_to_the_user() -> None:
    """The two properties that make the digest safe to store.

    A bare SHA-256 of a 6-digit code is reversible from a database leak in
    microseconds. Keying it with a secret that is *not* in the database removes
    the offline attack; binding it to the user id means a digest can never be
    replayed against a different account.
    """
    same_code = "042424"

    unkeyed = hashlib.sha256(same_code.encode()).hexdigest()
    assert hash_verification_code(same_code, secret=SECRET, user_id=1) != unkeyed

    assert hash_verification_code(same_code, secret=SECRET, user_id=1) != (
        hash_verification_code(same_code, secret="another-secret", user_id=1)
    )


def test_a_code_hashed_for_one_user_does_not_match_for_another() -> None:
    """The cross-account replay this binding exists to prevent."""
    digest = hash_verification_code("123456", secret=SECRET, user_id=1)

    assert code_matches("123456", digest, secret=SECRET, user_id=1) is True
    assert code_matches("123456", digest, secret=SECRET, user_id=2) is False
    assert code_matches("123456", digest, secret="other", user_id=1) is False


def test_code_matches_is_false_for_a_missing_digest() -> None:
    """Fail closed: with no code outstanding, nothing can be correct."""
    assert code_matches("123456", None, secret=SECRET, user_id=1) is False
    assert code_matches("123456", "", secret=SECRET, user_id=1) is False


def test_issue_phone_code_stores_a_hash_and_bounds_the_lifetime() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    issued = issue_phone_code(secret=SECRET, user_id=42, ttl_minutes=10, now=now)

    assert issued.code == f"{int(issued.code):06d}"  # six digits, zero-padded
    assert issued.code not in issued.code_hash
    assert issued.code_hash == hash_verification_code(
        issued.code, secret=SECRET, user_id=42
    )
    assert issued.sent_at == now
    assert issued.expires_at == now + timedelta(minutes=10)


def test_issue_phone_code_rejects_a_naive_now() -> None:
    """A naive/aware comparison fails deep in a request; fail at the call site."""
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_phone_code(secret=SECRET, user_id=1, now=datetime(2026, 9, 22, 12, 0))


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expiry_is_exactly_inclusive_at_the_deadline() -> None:
    """``<=`` (expired at the deadline) so this agrees with ``expires_at > now``.

    If the two disagreed, a code would pass the check and then silently match
    no row in the conditional UPDATE — an "invalid code" for one that had just
    been reported live.
    """
    deadline = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    assert code_is_expired(deadline, now=deadline) is True
    assert code_is_expired(deadline, now=deadline - timedelta(seconds=1)) is False
    assert code_is_expired(deadline, now=deadline + timedelta(seconds=1)) is True


def test_a_missing_expiry_is_treated_as_expired() -> None:
    """Fail closed: a code with no recorded deadline must not be valid forever."""
    assert code_is_expired(None) is True


def test_expiry_tolerates_a_naive_datetime_from_sqlite() -> None:
    """SQLite round-trips ``DateTime(timezone=True)`` as naive; Postgres does not."""
    naive_deadline = datetime(2026, 9, 22, 12, 0)

    assert code_is_expired(naive_deadline, now=datetime(2026, 9, 22, 12, 0, 1, tzinfo=UTC))
    assert not code_is_expired(
        naive_deadline, now=datetime(2026, 9, 22, 11, 59, 59, tzinfo=UTC)
    )


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_cooldown_boundary() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    # Exactly at the boundary the cooldown has elapsed (>=, not >).
    assert cooldown_elapsed(now - timedelta(seconds=60), now=now, cooldown_seconds=60)
    assert not cooldown_elapsed(
        now - timedelta(seconds=59), now=now, cooldown_seconds=60
    )
    assert not cooldown_elapsed(now, now=now, cooldown_seconds=60)


def test_never_sent_and_disabled_cooldown_always_allow_a_send() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    assert cooldown_elapsed(None, now=now, cooldown_seconds=60)
    assert cooldown_elapsed(now, now=now, cooldown_seconds=0)


def test_seconds_until_resend_is_reported_while_throttled() -> None:
    """The SPA runs its countdown from this, so a suppressed resend is visible."""
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    remaining = seconds_until_resend_allowed(
        now - timedelta(seconds=20), now=now, cooldown_seconds=60
    )

    assert remaining == 40
    assert seconds_until_resend_allowed(
        now - timedelta(seconds=60), now=now, cooldown_seconds=60
    ) is None


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


def test_attempts_boundary() -> None:
    assert attempts_exhausted(MAX_VERIFICATION_ATTEMPTS) is True
    assert attempts_exhausted(MAX_VERIFICATION_ATTEMPTS + 1) is True
    assert attempts_exhausted(MAX_VERIFICATION_ATTEMPTS - 1) is False
    assert attempts_exhausted(0) is False


def test_a_null_attempt_counter_does_not_lock_the_user_out() -> None:
    """A NULL column (legacy row) must mean "no attempts", not "exhausted"."""
    assert attempts_exhausted(None) is False


def test_attempt_ceiling_is_configurable() -> None:
    assert attempts_exhausted(2, max_attempts=2) is True
    assert attempts_exhausted(2, max_attempts=3) is False
