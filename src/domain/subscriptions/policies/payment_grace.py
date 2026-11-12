"""Payment grace-period policy — the single definition of "3 business days".

The business rule
-----------------
When a recurring subscription payment fails, the customer keeps their paid
entitlement for at most :data:`DEFAULT_GRACE_BUSINESS_DAYS` **business days**.
Once that window closes without a successful payment the paid entitlement is
revoked locally. Revoking entitlement is **not** cancelling the Stripe
subscription — see :class:`~src.domain.subscriptions.value_object.payment_state.PaymentState`.

Why the deadline is computed once and persisted
-----------------------------------------------
Every property the requirement demands follows from storing an absolute
instant rather than recomputing it:

* **Not extendable by replay.** A duplicate ``invoice.payment_failed`` cannot
  move an existing deadline because the transition is guarded on state, not on
  the event.
* **Restart-safe.** There is no in-memory timer to lose; downtime that spans the
  deadline is caught by the next read or sweep.
* **Recoverable.** A missed background tick cannot make the window *longer*
  than the policy, because readers derive entitlement from the stored deadline
  on every request.

The anchor
----------
The window is anchored to the **invoice's own creation instant**, not to the
delivery time of a webhook. Stripe retries dunning on the *same* invoice and
re-delivers events; anchoring to delivery would push the deadline back on every
retry, turning a three-day limit into however long Stripe keeps retrying.

Day counting
------------
Only Monday–Friday count. Holidays are supported through an explicit parameter
rather than a hidden global, so a deployment can adopt a calendar without
changing this module. The deadline is the **end** of the Nth business day in the
configured billing timezone: a failure on Friday gives Monday, Tuesday and
Wednesday, and the customer keeps access until Wednesday ends.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.domain.subscriptions.value_object.payment_state import PaymentState
from src.domain.subscriptions.value_object.tier import SubscriptionTier

#: The product rule. Three business days, deliberately a constant rather than a
#: per-call argument so no caller can quietly choose a longer window.
DEFAULT_GRACE_BUSINESS_DAYS: int = 3

#: Billing timezone used to decide what a "day" is. UTC by default: the product
#: bills worldwide and Stripe reports timestamps in UTC, so UTC is the only
#: definition that cannot vary by customer.
DEFAULT_GRACE_TIMEZONE: str = "UTC"


def parse_holidays(raw: str | Iterable[str] | None) -> frozenset[date]:
    """Parse a comma-separated list of ISO dates into a holiday set.

    Blank entries are ignored, so an unset setting and an empty string behave
    identically. A malformed entry raises ``ValueError`` rather than being
    skipped: a typo in a holiday calendar would silently grant extra paid days,
    which is the expensive direction to get wrong.
    """
    if raw is None:
        return frozenset()
    entries = raw.split(",") if isinstance(raw, str) else raw
    holidays: set[date] = set()
    for entry in entries:
        text = entry.strip()
        if not text:
            continue
        holidays.add(date.fromisoformat(text))
    return frozenset(holidays)


def is_business_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    """Return True when ``day`` is a weekday that is not a configured holiday."""
    return day.weekday() < 5 and day not in holidays


def add_business_days(
    start: date,
    days: int,
    holidays: frozenset[date] = frozenset(),
) -> date:
    """Return the date ``days`` business days after ``start``.

    Counting begins on the **next** calendar day, so a Friday failure followed
    by three business days lands on Wednesday. ``days=0`` returns ``start``.

    Raises:
        ValueError: If ``days`` is negative.
    """
    if days < 0:
        raise ValueError("days cannot be negative.")

    day = start
    added = 0
    while added < days:
        day += timedelta(days=1)
        if is_business_day(day, holidays):
            added += 1
    return day


def grace_deadline(
    anchor: datetime,
    *,
    business_days: int = DEFAULT_GRACE_BUSINESS_DAYS,
    timezone: str = DEFAULT_GRACE_TIMEZONE,
    holidays: frozenset[date] = frozenset(),
) -> datetime:
    """Return the inclusive deadline of a grace window starting at ``anchor``.

    The result is an aware UTC instant: the end of the final business day
    (``23:59:59.999999``) in ``timezone``, converted to UTC. Constructing the
    end-of-day through :class:`ZoneInfo` keeps the result correct across a
    daylight-saving transition, where a naive offset arithmetic would be an
    hour out.

    Args:
        anchor: Instant the failure was recorded (normally the invoice's
            creation time). Must be timezone-aware.
        business_days: Length of the window. Defaults to the product rule.
        timezone: IANA zone that defines the business day.
        holidays: Dates that do not count as business days.

    Raises:
        ValueError: If ``anchor`` is naive or ``business_days`` is negative.
    """
    if anchor.tzinfo is None or anchor.tzinfo.utcoffset(anchor) is None:
        raise ValueError(
            "anchor must be timezone-aware; pass the invoice instant in UTC."
        )

    zone = ZoneInfo(timezone)
    local_date = anchor.astimezone(zone).date()
    final_day = add_business_days(local_date, business_days, holidays)
    end_of_day = datetime.combine(
        final_day, time(23, 59, 59, 999999), tzinfo=zone
    )
    return end_of_day.astimezone(UTC)


def entitlement_is_live(
    *,
    payment_state: str | PaymentState | None,
    grace_deadline_at: datetime | None,
    now: datetime,
) -> bool:
    """Return True when the paid entitlement is still in force.

    This is the **whole** time-based rule, in one pure function, so every caller
    derives entitlement identically. It is deliberately conservative:

    * A row in ``GRACE`` with **no** deadline is treated as expired. A missing
      deadline is a data defect, and the safe reading of a defect is "no paid
      access", never "paid forever" — the bug this module exists to prevent.
    * Any other state (``OK``, ``REVOKED``, unknown, ``None``) is not
      time-bounded *here*; ``REVOKED`` is excluded because the stored tier has
      already been lowered, and an unknown state is rejected as expired for the
      same conservative reason.
    """
    state = (
        payment_state.value
        if isinstance(payment_state, PaymentState)
        else (payment_state or PaymentState.OK.value)
    )

    if state == PaymentState.OK.value:
        return True
    if state == PaymentState.REVOKED.value:
        return False
    if state == PaymentState.GRACE.value:
        if grace_deadline_at is None:
            return False
        return now < grace_deadline_at
    # Unrecognised state: never grant paid access on a value we do not
    # understand.
    return False


def effective_tier(
    *,
    tier: SubscriptionTier,
    payment_state: str | PaymentState | None,
    grace_deadline_at: datetime | None,
    stripe_status: str | None,
    now: datetime,
) -> SubscriptionTier:
    """Derive what the customer may actually use, from persisted state.

    Args:
        tier: The plan the customer bought.
        payment_state: Persisted :class:`PaymentState` value.
        grace_deadline_at: Persisted deadline (set only while in grace).
        stripe_status: Last Stripe status mirrored onto the row.
        now: Evaluation instant, timezone-aware UTC.

    Returns:
        ``tier`` when the subscription is paid and current (or still inside its
        grace window), otherwise :attr:`SubscriptionTier.FREE`.
    """
    if tier == SubscriptionTier.FREE or tier == SubscriptionTier.GUEST:
        return tier

    if stripe_status is not None and stripe_status.lower() in {
        "canceled",
        "incomplete",
        "incomplete_expired",
    }:
        return SubscriptionTier.FREE

    live = entitlement_is_live(
        payment_state=payment_state,
        grace_deadline_at=grace_deadline_at,
        now=now,
    )
    return tier if live else SubscriptionTier.FREE
