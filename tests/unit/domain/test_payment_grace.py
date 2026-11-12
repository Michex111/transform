"""Unit tests for the payment grace-period policy.

These pin the *business rule* — "at most three business days" — rather than
implementation details, because the whole point of the policy module is that
every caller derives the same answer from it.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domain.subscriptions.policies.payment_grace import (
    DEFAULT_GRACE_BUSINESS_DAYS,
    add_business_days,
    effective_tier,
    entitlement_is_live,
    grace_deadline,
    is_business_day,
    parse_holidays,
)
from src.domain.subscriptions.value_object.payment_state import PaymentState
from src.domain.subscriptions.value_object.tier import SubscriptionTier


# 2026-11-06 is a Friday and 2026-11-11 a Wednesday (the audit's reference date).
FRIDAY = date(2026, 11, 6)
SATURDAY = date(2026, 11, 7)
WEDNESDAY = date(2026, 11, 11)


def test_the_product_rule_is_three_business_days() -> None:
    assert DEFAULT_GRACE_BUSINESS_DAYS == 3


# --- day counting ---------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        # Friday + 1 = Monday, so +3 lands on Wednesday.
        (FRIDAY, date(2026, 11, 11)),
        # A Saturday failure is treated as starting the following Monday.
        (SATURDAY, date(2026, 11, 11)),
        # Wednesday + 3 = Monday, because the weekend is skipped.
        (WEDNESDAY, date(2026, 11, 16)),
        # Thursday + 3 also crosses a weekend.
        (date(2026, 11, 5), date(2026, 11, 10)),
    ],
)
def test_add_business_days_skips_weekends(start: date, expected: date) -> None:
    assert add_business_days(start, 3) == expected


def test_zero_business_days_is_the_anchor_day() -> None:
    assert add_business_days(FRIDAY, 0) == FRIDAY


def test_negative_business_days_is_rejected() -> None:
    with pytest.raises(ValueError):
        add_business_days(FRIDAY, -1)


def test_configured_holidays_do_not_count() -> None:
    """A holiday inside the window pushes the deadline out by one business day.

    This is opt-in; the default holiday set is empty and the requirement is
    weekends-only.
    """
    holidays = frozenset({date(2026, 11, 9)})  # Monday
    assert add_business_days(FRIDAY, 3, holidays) == date(2026, 11, 12)


def test_is_business_day_excludes_weekends_and_holidays() -> None:
    assert is_business_day(FRIDAY)
    assert not is_business_day(SATURDAY)
    assert not is_business_day(date(2026, 11, 8))  # Sunday
    assert not is_business_day(FRIDAY, frozenset({FRIDAY}))


def test_parse_holidays_ignores_blank_entries_and_rejects_garbage() -> None:
    assert parse_holidays("") == frozenset()
    assert parse_holidays(" 2026-12-25 , 2026-12-26 ") == frozenset(
        {date(2026, 12, 25), date(2026, 12, 26)}
    )
    with pytest.raises(ValueError):
        parse_holidays("25/12/2026")


# --- deadline -------------------------------------------------------------


def test_deadline_is_the_end_of_the_third_business_day() -> None:
    anchor = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)  # Friday afternoon
    deadline = grace_deadline(anchor)

    assert deadline == datetime(2026, 11, 11, 23, 59, 59, 999999, tzinfo=UTC)
    # The customer keeps access for the whole third day.
    assert deadline > datetime(2026, 11, 11, 23, 0, tzinfo=UTC)


def test_deadline_crosses_a_year_boundary() -> None:
    anchor = datetime(2026, 12, 30, 9, 0, tzinfo=UTC)  # Wednesday
    # Thu 31, Fri 1 Jan, Mon 4 Jan.
    assert grace_deadline(anchor) == datetime(
        2027, 1, 4, 23, 59, 59, 999999, tzinfo=UTC
    )


def test_deadline_is_dst_correct_in_the_billing_timezone() -> None:
    """A deadline spanning a DST change is still end-of-day local time.

    New York moves from EDT to EST on 2026-11-01. A Friday 2026-10-30 anchor has
    its window end after the transition, so the UTC instant must reflect the new
    offset (-05:00), not the old one. Naive offset arithmetic would be an hour
    out.
    """
    anchor = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # Friday
    deadline = grace_deadline(anchor, timezone="America/New_York")

    local = deadline.astimezone(ZoneInfo("America/New_York"))
    assert local.date() == date(2026, 11, 4)  # Mon 2, Tue 3, Wed 4
    assert (local.hour, local.minute) == (23, 59)
    # EST, not EDT.
    assert deadline.utcoffset() == timedelta(0)
    assert local.utcoffset() == timedelta(hours=-5)


def test_naive_anchor_is_rejected() -> None:
    with pytest.raises(ValueError):
        grace_deadline(datetime(2026, 11, 6, 14, 0))


# --- entitlement derivation ----------------------------------------------


def test_ok_state_is_live() -> None:
    assert entitlement_is_live(
        payment_state=PaymentState.OK,
        grace_deadline_at=None,
        now=datetime(2026, 11, 11, tzinfo=UTC),
    )


def test_grace_is_live_before_the_deadline_only() -> None:
    deadline = datetime(2026, 11, 11, 23, 59, 59, 999999, tzinfo=UTC)
    assert entitlement_is_live(
        payment_state=PaymentState.GRACE,
        grace_deadline_at=deadline,
        now=deadline - timedelta(seconds=1),
    )
    # At the deadline exactly, the window has closed.
    assert not entitlement_is_live(
        payment_state=PaymentState.GRACE, grace_deadline_at=deadline, now=deadline
    )


def test_grace_without_a_deadline_is_treated_as_expired() -> None:
    """A missing deadline is a data defect, and the safe reading is "no access".

    The opposite default is precisely the bug this feature exists to remove:
    a subscription that stays entitled because nothing computed an end.
    """
    assert not entitlement_is_live(
        payment_state=PaymentState.GRACE,
        grace_deadline_at=None,
        now=datetime(2026, 11, 11, tzinfo=UTC),
    )


def test_revoked_and_unknown_states_are_not_live() -> None:
    now = datetime(2026, 11, 11, tzinfo=UTC)
    assert not entitlement_is_live(
        payment_state=PaymentState.REVOKED, grace_deadline_at=None, now=now
    )
    assert not entitlement_is_live(
        payment_state="WAT", grace_deadline_at=None, now=now
    )


def test_effective_tier_drops_to_free_once_grace_closes() -> None:
    deadline = datetime(2026, 11, 11, 23, 59, 59, 999999, tzinfo=UTC)
    kwargs = dict(
        tier=SubscriptionTier.PRO,
        payment_state=PaymentState.GRACE,
        grace_deadline_at=deadline,
        stripe_status="past_due",
    )
    assert (
        effective_tier(now=deadline - timedelta(minutes=1), **kwargs)
        == SubscriptionTier.PRO
    )
    assert effective_tier(now=deadline, **kwargs) == SubscriptionTier.FREE


def test_effective_tier_is_free_for_a_revoked_row() -> None:
    assert (
        effective_tier(
            tier=SubscriptionTier.PRO_PLUS,
            payment_state=PaymentState.REVOKED,
            grace_deadline_at=None,
            stripe_status="past_due",
            now=datetime(2026, 11, 11, tzinfo=UTC),
        )
        == SubscriptionTier.FREE
    )


def test_effective_tier_is_free_for_a_terminal_stripe_status() -> None:
    """A stale paid tier cannot outvote Stripe's terminal status."""
    for status in ("canceled", "incomplete", "incomplete_expired"):
        assert (
            effective_tier(
                tier=SubscriptionTier.PRO,
                payment_state=PaymentState.OK,
                grace_deadline_at=None,
                stripe_status=status,
                now=datetime(2026, 11, 11, tzinfo=UTC),
            )
            == SubscriptionTier.FREE
        )


def test_free_and_guest_tiers_are_returned_unchanged() -> None:
    now = datetime(2026, 11, 11, tzinfo=UTC)
    assert (
        effective_tier(
            tier=SubscriptionTier.FREE,
            payment_state=PaymentState.OK,
            grace_deadline_at=None,
            stripe_status="active",
            now=now,
        )
        == SubscriptionTier.FREE
    )
    assert (
        effective_tier(
            tier=SubscriptionTier.GUEST,
            payment_state=PaymentState.OK,
            grace_deadline_at=None,
            stripe_status=None,
            now=now,
        )
        == SubscriptionTier.GUEST
    )
