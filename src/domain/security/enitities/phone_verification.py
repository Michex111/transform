"""Phone-verification code rules.

The flow is a mirror of email verification (``email_verification.py``) with one
property that changes the threat model completely: a 6-digit code has roughly
20 bits of entropy, where a 43-character URL-safe token has 256.

That single difference drives everything here:

* **The digest is keyed and bound to the account.** A bare SHA-256 of a 6-digit
  code is reversible in microseconds — an attacker with a read-only database
  leak could precompute the entire keyspace, find the row, and verify any
  account. Keying the HMAC with ``SECRET_KEY`` (which does not live in the
  database) removes the *offline* attack entirely, and binding the message to
  the user id means a digest computed for one account can never be replayed
  against another even if two users are issued the same code. This is the
  reason the function signature takes ``secret`` and ``user_id`` and not just
  the code. Do not "simplify" it to a plain hash.
* **A short expiry.** Ten minutes by default. The code survives an SMS delivery
  delay and a slow user, and bounds how long a leaked message is usable.
* **A hard attempt ceiling.** With 10^6 codes, a few thousand guesses find the
  code. ``phone_verification_attempts`` is the only control that stops online
  guessing, so it is enforced server-side and never resettable by the client.
* **A silent resend cooldown.** Prevents the endpoint from becoming an SMS
  bomb (which costs the operator money) and stops it being a probe: a suppressed
  resend answers exactly like a successful one.
* **No sign-in gate.** Unlike email — which is the account-recovery channel —
  an unverified phone number does not block sign-in. Gating on it would create
  a lockout risk (the operator's SMS provider going down locks every user out)
  with no security benefit, because the phone is only an opt-in convenience
  detail. Documented here because the absence of a gate is a decision, not an
  oversight.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.domain.security.exceptions import InvalidPhoneNumber

#: Digits in a generated code. Fixed at 6 (SMS-length, familiar, and the value
#: ``generate_verification_code`` pads to).
PHONE_CODE_DIGITS = 6

#: Default lifetime of an issued code.
DEFAULT_TTL_MINUTES = 10

#: Default minimum gap between two sends for the same account.
DEFAULT_RESEND_COOLDOWN_SECONDS = 60

#: Default number of failed attempts a code tolerates before it is burned.
MAX_VERIFICATION_ATTEMPTS = 5

#: E.164: ``+`` then a non-zero country digit, then 7-14 more digits (so 8-15
#: digits after the ``+``). Anchored so a partially-normalised string cannot
#: slip through.
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

#: Characters removed before validation — the formatting humans actually type
#: (spaces, hyphens, parentheses, dots). Separators are stripped rather than
#: rejected so ``+1 (415) 555-2671`` is accepted as typed.
_STRIP_CHARS = str.maketrans({c: None for c in " -().\t"})


def normalize_phone_number(raw: str) -> str:
    """Return ``raw`` as an E.164 string, or raise ``InvalidPhoneNumber``.

    Normalisation is deliberately conservative: it removes the cosmetic
    separators people type, translates the international dialling prefix
    (``00`` → ``+``, which is why the leading-zero conversion is applied
    *after* separator removal so ``00 44 …`` works), then validates the strict
    E.164 shape. Anything else — letters, a missing ``+``, too few or too many
    digits — is rejected rather than guessed at, because a wrong guess here
    sends a paid SMS to a stranger.
    """
    if raw is None:
        raise InvalidPhoneNumber("A phone number is required.")

    cleaned = raw.strip().translate(_STRIP_CHARS)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if not _E164_RE.match(cleaned):
        raise InvalidPhoneNumber(
            "Enter a phone number in international format, e.g. +14155552671."
        )
    return cleaned


def is_e164(value: str) -> bool:
    """True when ``value`` is already a well-formed E.164 number."""
    return bool(value) and _E164_RE.match(value) is not None


def generate_verification_code() -> str:
    """Return a fresh ``PHONE_CODE_DIGITS``-digit code, zero-padded.

    ``randbelow`` rather than ``randint`` so the range is exactly 10^6 and the
    padding restores leading zeros — ``str(secrets.randbelow(10**6))`` would
    turn ``000123`` into ``"123"``, which is both a different code and a
    shorter one.
    """
    return f"{secrets.randbelow(10**PHONE_CODE_DIGITS):0{PHONE_CODE_DIGITS}d}"


def hash_verification_code(code: str, *, secret: str, user_id: int) -> str:
    """Return the storable HMAC-SHA256 digest of ``code`` for ``user_id``.

    See the module docstring: a 6-digit code has ~20 bits of entropy, so a
    plain digest of it is brute-forceable offline by anyone who reads the
    column. Keying the HMAC with ``SECRET_KEY`` (never stored in the database)
    makes precomputation useless, and including ``user_id`` in the message ties
    the digest to the account, so the same code issued to two users never
    produces the same stored value.
    """
    message = f"{user_id}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def code_matches(
    code: str,
    code_hash: str | None,
    *,
    secret: str,
    user_id: int,
) -> bool:
    """Constant-time comparison of ``code`` against a stored digest.

    ``hmac.compare_digest`` rather than ``==`` so the comparison time does not
    leak how many leading digits of the code were right — with only 10^6
    possibilities, a timing side channel would measurably shrink the search.

    A missing digest is a non-match (fail closed): there is no code outstanding,
    so nothing can be correct.
    """
    if not code_hash:
        return False
    candidate = hash_verification_code(code, secret=secret, user_id=user_id)
    return hmac.compare_digest(candidate, code_hash)


def code_is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    """True when ``expires_at`` has been reached (or is missing).

    A missing expiry is treated as expired — fail closed. That state should be
    unreachable, but if a row ever ends up without one, accepting the code
    would make it valid forever.

    Uses ``<=`` so a code whose deadline is *exactly now* counts as expired.
    This must agree with the conditional UPDATE in the users router, which
    filters on ``phone_verification_expires_at > now`` and therefore also
    excludes the exact deadline. If the two disagreed, the request would pass
    this check and then silently fail to match any row — a misleading "invalid
    code" for a code that had just been reported live.
    """
    if expires_at is None:
        return True
    reference = now or datetime.now(UTC)
    expires = _as_aware(expires_at)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return expires <= reference


def cooldown_elapsed(
    sent_at: datetime | None,
    *,
    now: datetime | None = None,
    cooldown_seconds: int = DEFAULT_RESEND_COOLDOWN_SECONDS,
) -> bool:
    """True when a new code may be sent for this account.

    ``sent_at`` of ``None`` (never sent) always permits a send, as does a
    non-positive cooldown — so a deployment that wants no throttling can
    express that without a separate code path.
    """
    if sent_at is None or cooldown_seconds <= 0:
        return True
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    previous = _as_aware(sent_at)
    return reference >= previous + timedelta(seconds=cooldown_seconds)


def seconds_until_resend_allowed(
    sent_at: datetime | None,
    *,
    now: datetime | None = None,
    cooldown_seconds: int = DEFAULT_RESEND_COOLDOWN_SECONDS,
) -> int | None:
    """Seconds left in the resend cooldown, or ``None`` when a send is allowed.

    Returned to the SPA so it can run its own countdown instead of polling —
    and so a suppressed resend is still visibly a *throttled* one rather than
    an unexplained no-op.
    """
    if cooldown_elapsed(sent_at, now=now, cooldown_seconds=cooldown_seconds):
        return None
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    previous = _as_aware(sent_at)
    remaining = (previous + timedelta(seconds=cooldown_seconds)) - reference
    return max(0, int(remaining.total_seconds()))


def attempts_exhausted(attempts: int | None, *, max_attempts: int = MAX_VERIFICATION_ATTEMPTS) -> bool:
    """True when the code has run out of guesses and must be re-issued.

    ``None`` is treated as zero (no attempts recorded yet) so a legacy/NULL
    column cannot accidentally lock a user out of a code they just received.
    """
    return (attempts or 0) >= max_attempts


def _as_aware(value: datetime) -> datetime:
    """Attach UTC to a naive datetime.

    SQLite (used by the test suite) round-trips ``DateTime(timezone=True)`` as
    a naive value while PostgreSQL returns it aware. Normalising here rather
    than at each call site keeps the comparison from raising ``TypeError`` deep
    inside a request handler.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass(frozen=True)
class IssuedPhoneCode:
    """A freshly minted code: the value to send, and what to store."""

    #: The digits that go into the SMS. Never persisted.
    code: str
    #: HMAC-SHA256 hex digest, the only form written to the database.
    code_hash: str
    #: When the code was issued (drives the resend cooldown).
    sent_at: datetime
    #: Instant after which the code is rejected.
    expires_at: datetime


def issue_phone_code(
    *,
    secret: str,
    user_id: int,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    now: datetime | None = None,
) -> IssuedPhoneCode:
    """Mint a new code valid for ``ttl_minutes``.

    ``now`` is injectable so expiry and cooldown behaviour are testable without
    patching the clock. Naive datetimes are rejected rather than silently
    assumed to be UTC: a naive/aware comparison raises ``TypeError`` deep in a
    request handler, which is a far worse failure than a clear error here.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        raise ValueError("issue_phone_code requires a timezone-aware 'now'")

    code = generate_verification_code()
    return IssuedPhoneCode(
        code=code,
        code_hash=hash_verification_code(code, secret=secret, user_id=user_id),
        sent_at=reference,
        expires_at=reference + timedelta(minutes=ttl_minutes),
    )
