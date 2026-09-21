"""Monthly credit period arithmetic.

Monthly conversion credits are bucketed by **UTC calendar month**: the bucket
key is the ``"%Y-%m"`` string of the month (for example ``"2026-09"``), stored
as ``Credit.period_key``. A new calendar month implicitly starts a fresh
bucket, so callers fall back to the tier allowance whenever no bucket exists
for the current key.

Both the bucketing key and the instant at which a user's credits reset must be
derived from the same arithmetic. If they were computed independently the reset
date shown to users could drift from the period that actually holds their
credits — for example across a midnight or a year boundary. This module is the
single source of truth for "which period is it" and "when does the next one
start".
"""

from datetime import UTC, datetime


def _to_utc(now: datetime) -> datetime:
    """Normalise an instant to timezone-aware UTC.

    Args:
        now: Instant to normalise.

    Returns:
        The same instant expressed in UTC.

    Raises:
        ValueError: If ``now`` is naive (its UTC month is ambiguous, which is
            exactly the drift this module exists to prevent).
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware; pass datetime.now(UTC) or an aware value.")
    return now.astimezone(UTC)


def _resolve(now: datetime | None) -> datetime:
    """Return the instant to compute from, defaulting to now in UTC."""
    return datetime.now(UTC) if now is None else _to_utc(now)


def current_period_key(now: datetime | None = None) -> str:
    """The "%Y-%m" accounting key for the period containing `now`.

    Args:
        now: Instant to bucket. Defaults to the current UTC time; passing it
            explicitly keeps callers (and tests) deterministic.

    Returns:
        The UTC calendar month of ``now`` formatted as ``"YYYY-MM"``.
    """
    instant = _resolve(now)
    return f"{instant.year:04d}-{instant.month:02d}"


def next_period_start(now: datetime | None = None) -> datetime:
    """First instant of the calendar month AFTER `now`'s month, tz-aware UTC.

    Args:
        now: Instant whose period precedes the returned instant. Defaults to
            the current UTC time; passing it explicitly keeps callers (and
            tests) deterministic.

    Returns:
        Midnight UTC on the first day of the next calendar month. December
        rolls over into January of the following year.
    """
    instant = _resolve(now)
    # Month arithmetic is done on an ordinal month index — never
    # ``timedelta(days=30)`` — so month lengths (including February in a leap
    # year) and the December -> January year rollover are always correct.
    months_since_year_zero = instant.year * 12 + (instant.month - 1) + 1
    next_year, next_month_index = divmod(months_since_year_zero, 12)
    return datetime(next_year, next_month_index + 1, 1, tzinfo=UTC)
