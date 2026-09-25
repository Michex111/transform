"""Password-reset token policy.

A reset token is the same *kind* of credential as an email-verification token:
high-entropy CSPRNG bytes, persisted only as a SHA-256 digest, single-use and
time-boxed. Rather than reimplement those properties, the primitives are shared
with ``one_time_token`` — one digest, expiry and cooldown implementation to
audit, instead of two that can drift apart and only one of which gets reviewed.

What genuinely differs, and therefore lives here, is the lifetime. A
verification link only proves an address; a reset link **is a full
account-takeover credential** — following it sets a new password with no
further authentication. So its window is deliberately far shorter than the
day-long verification window: the shorter the TTL, the smaller the window in
which a leaked mailbox, a shared screen or a browser-history entry is
exploitable. The rest of the reset design (single-use consumption, the resend
cooldown, the refusal to disclose whether an address exists) follows the same
reasoning as verification and reuses the same code.
"""

from datetime import datetime, timedelta

from src.domain.security.enitities.one_time_token import IssuedToken, issue_token

#: How long a reset link stays valid, in minutes. Short on purpose — see the
#: module docstring: a reset token grants full account takeover.
DEFAULT_TTL_MINUTES = 60


def issue_password_reset_token(
    *,
    ttl_minutes: int,
    now: datetime | None = None,
) -> IssuedToken:
    """Mint a password-reset token valid for ``ttl_minutes``.

    A non-positive TTL is rejected rather than silently producing a link that is
    already dead (or, worse, one whose expiry is in the past and only *looks*
    like a configured value to an operator). A reset link nobody can use is a
    support burden, not a security feature.

    ``now`` is injectable so expiry is testable without patching the clock, and
    must be timezone-aware for the same reason as ``issue_token`` — a
    naive/aware comparison would raise deep inside a request handler.
    """
    if ttl_minutes <= 0:
        raise ValueError("issue_password_reset_token requires a positive ttl_minutes")
    return issue_token(ttl=timedelta(minutes=ttl_minutes), now=now)
