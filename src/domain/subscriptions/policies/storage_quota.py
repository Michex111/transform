"""Pure storage-quota arithmetic.

This module holds the *rule* for whether an upload fits in an account's storage
quota, with no I/O, no ORM and no HTTP in sight. It exists as a separate unit
because the same rule is applied twice by design:

* **Pre-flight** (session creation, when the client declares a size) — advisory
  UX only. It fails BEFORE a multi-gigabyte transfer starts, which is the whole
  point of asking the client how big the file is.
* **Authoritative** (finalize, where the object's real size is measured) — this
  is the check that establishes the invariant.

The pre-flight trusts a client-supplied number, so it can be lied to; the
finalize check measures the bytes that actually exist. Sharing one function
means the two can never drift into disagreeing about the boundary — see
``FileService.authorize_upload_size`` and ``FileService.complete_upload``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StorageQuotaDecision:
    """The outcome of evaluating one upload against one quota.

    All values are bytes. ``available_bytes`` is never negative: an account
    already over quota reports zero available rather than a negative number,
    because a negative "available" would render as nonsense in the UI and would
    make ``file_size <= available_bytes`` accidentally true for a zero-byte
    file.
    """

    limit_bytes: int
    used_bytes: int
    file_size: int

    @property
    def available_bytes(self) -> int:
        """Remaining headroom, clamped at zero."""
        return max(0, self.limit_bytes - self.used_bytes)

    @property
    def allowed(self) -> bool:
        """True when ``file_size`` fits in the remaining headroom.

        Boundary: a file that exactly fills the remaining space is allowed
        (``used + file_size == limit``), so the quota is a hard ceiling, not an
        exclusive bound.
        """
        return self.file_size <= self.available_bytes


def evaluate_storage_quota(
    *, limit_bytes: int, used_bytes: int, file_size: int,
) -> StorageQuotaDecision:
    """Evaluate ``file_size`` against ``limit_bytes`` minus ``used_bytes``.

    Every input is coerced to ``int`` before the decision is built. That is not
    cosmetic tidiness — it is a fix for a real production-only failure.

    ``used_bytes`` comes from ``SUM(file_size_bytes)``, which **PostgreSQL
    returns as a ``Decimal``** while SQLite returns a plain ``int``. The
    decision's numbers are embedded in the structured 413 body an over-quota
    upload gets back, and FastAPI's JSON encoder cannot serialise a ``Decimal``,
    so the refusal raised instead of responding: the API answered **500**
    instead of "not enough storage", and no SQLite-backed test could see it.
    Normalising here means *every* caller of this function — the advisory
    pre-flight and the authoritative finalize check — produces JSON-safe
    integers no matter which database or repository fed it.

    Raises:
        ValueError: if any input is negative. A negative limit or usage is a
            caller bug (a mis-read column, a missing ``coalesce``); silently
            clamping it would hide the bug behind a passing quota check.
    """
    limit = int(limit_bytes)
    used = int(used_bytes)
    size = int(file_size)
    if limit < 0:
        raise ValueError("limit_bytes cannot be negative.")
    if used < 0:
        raise ValueError("used_bytes cannot be negative.")
    if size < 0:
        raise ValueError("file_size cannot be negative.")
    return StorageQuotaDecision(limit_bytes=limit, used_bytes=used, file_size=size)
