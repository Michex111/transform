"""Tests for the shared monthly credit period arithmetic.

These are the single source of truth for which UTC calendar month a credit
bucket belongs to and when the next bucket starts, so they are exercised
against month ends, leap Februaries, year rollover, and the midnight boundary.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    next_period_start,
)


def _ts(value: str) -> datetime:
    """Parse an ISO-8601 test timestamp into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{value} must be timezone-aware"
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# current_period_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-09-20T12:00:00+00:00", "2026-09"),  # mid-month
        ("2026-09-01T00:00:00+00:00", "2026-09"),  # first day of a month
        ("2026-01-31T00:00:00+00:00", "2026-01"),  # last day of a 31-day month
        ("2026-09-30T23:00:00+00:00", "2026-09"),  # last day of a 30-day month
        ("2026-12-31T23:59:59+00:00", "2026-12"),  # last day of a 31-day December
        ("2027-01-01T00:00:00+00:00", "2027-01"),  # New Year
        ("2028-02-29T10:00:00+00:00", "2028-02"),  # leap day
        ("2026-01-31T23:59:59+00:00", "2026-01"),  # one second before midnight
        ("2026-02-01T00:00:00+00:00", "2026-02"),  # exactly midnight
    ],
)
def test_current_period_key_uses_the_utc_calendar_month(now: str, expected: str) -> None:
    assert current_period_key(_ts(now)) == expected


def test_current_period_key_zero_pads_single_digit_months() -> None:
    assert current_period_key(datetime(2026, 1, 5, tzinfo=UTC)) == "2026-01"


def test_current_period_key_normalises_non_utc_offsets_to_utc() -> None:
    """A local timestamp is bucketed by its UTC month, not its local month."""
    # 2026-01-01 00:00 +05:00 is still 2025-12-31 19:00 UTC.
    assert current_period_key(datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=5)))) == "2025-12"


def test_current_period_key_defaults_to_now_in_utc() -> None:
    """The default is the current UTC month.

    The expected value is computed from a captured instant (and its
    independent ``strftime`` oracle) either side of the call so the test
    cannot flake when it happens to run across a month boundary.
    """
    before = datetime.now(UTC)
    key = current_period_key()
    after = datetime.now(UTC)
    assert key in {before.strftime("%Y-%m"), after.strftime("%Y-%m")}


def test_current_period_key_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        current_period_key(datetime(2026, 9, 20, 12, 0))


# ---------------------------------------------------------------------------
# next_period_start
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-09-20T12:00:00+00:00", "2026-10-01T00:00:00+00:00"),  # mid-month
        ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),  # first day of a month
        ("2026-09-30T23:59:59+00:00", "2026-10-01T00:00:00+00:00"),  # last day of a 30-day month
        ("2026-01-31T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),  # last day of a 31-day month
        ("2026-06-30T23:59:59+00:00", "2026-07-01T00:00:00+00:00"),  # H1 -> H2
        ("2026-12-31T23:59:59+00:00", "2027-01-01T00:00:00+00:00"),  # December -> January
        ("2026-12-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),  # December mid-month
        ("2027-01-01T00:00:00+00:00", "2027-02-01T00:00:00+00:00"),  # January -> February
        ("2027-02-28T23:59:59+00:00", "2027-03-01T00:00:00+00:00"),  # non-leap February
        ("2028-02-15T00:00:00+00:00", "2028-03-01T00:00:00+00:00"),  # leap February
        ("2028-02-29T23:59:59+00:00", "2028-03-01T00:00:00+00:00"),  # leap day
        ("2026-01-31T23:59:59+00:00", "2026-02-01T00:00:00+00:00"),  # one second before midnight
        ("2026-02-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"),  # exactly midnight
    ],
)
def test_next_period_start_is_the_first_instant_of_the_next_month(now: str, expected: str) -> None:
    assert next_period_start(_ts(now)) == _ts(expected)


def test_next_period_start_handles_year_rollover_across_31_day_december() -> None:
    result = next_period_start(datetime(2026, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC))
    assert result == datetime(2027, 1, 1, tzinfo=UTC)
    assert (result.year, result.month, result.day) == (2027, 1, 1)


def test_next_period_start_is_strictly_after_now() -> None:
    for now in (
        datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
    ):
        assert next_period_start(now) > now


def test_next_period_start_normalises_non_utc_offsets_to_utc() -> None:
    """The next period is computed from the UTC month, not the local one."""
    # 2026-01-01 04:00 +05:00 == 2025-12-31 23:00 UTC -> next period is 2026-01.
    result = next_period_start(datetime(2026, 1, 1, 4, 0, tzinfo=timezone(timedelta(hours=5))))
    assert result == datetime(2026, 1, 1, tzinfo=UTC)


def test_next_period_start_is_timezone_aware_utc() -> None:
    result = next_period_start(datetime(2026, 9, 20, 12, 0, tzinfo=UTC))
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result.tzinfo is UTC
    assert (result.hour, result.minute, result.second, result.microsecond) == (0, 0, 0, 0)


def test_next_period_start_defaults_to_now_in_utc() -> None:
    before = datetime.now(UTC)
    result = next_period_start()
    after = datetime.now(UTC)

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result.day == 1
    assert (result.hour, result.minute, result.second, result.microsecond) == (0, 0, 0, 0)
    # Derived from the current UTC month (never a hard-coded gap).
    assert result in {next_period_start(before), next_period_start(after)}


def test_next_period_start_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_period_start(datetime(2026, 9, 20, 12, 0))


# ---------------------------------------------------------------------------
# The two helpers agree: the reset instant opens the period AFTER the key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "now",
    [
        "2026-09-20T12:00:00+00:00",
        "2026-12-31T23:59:59+00:00",
        "2027-01-01T00:00:00+00:00",
        "2028-02-15T00:00:00+00:00",
        "2026-01-31T23:59:59+00:00",
    ],
)
def test_next_period_start_opens_exactly_the_period_after_the_current_key(now: str) -> None:
    instant = _ts(now)
    start = next_period_start(instant)

    # The reset instant is in the month right after `now`'s UTC month ...
    expected_year, expected_month = (
        (instant.year + 1, 1) if instant.month == 12 else (instant.year, instant.month + 1)
    )
    assert current_period_key(start) == f"{expected_year:04d}-{expected_month:02d}"
    assert current_period_key(start) != current_period_key(instant)

    # ... and it is the exact boundary: the last microsecond before it still
    # belongs to the bucket that holds `now`.
    assert current_period_key(start - timedelta(microseconds=1)) == current_period_key(instant)
