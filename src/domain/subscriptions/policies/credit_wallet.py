"""Credit wallet rules: three populations, two different lifetimes.

Before this module there was one number. ``monthly_credits.remaining`` held
plan credits and purchased credits merged together, which is why the webhook had
to say "never lower the allowance, or we destroy something the user paid for" —
it could not tell the two apart. Splitting them is what makes the rules below
expressible at all.

The three populations:

======== ============================== ================================================
Kind     Lifetime                       Reset
======== ============================== ================================================
plan     current billing period         falls back to the tier grant each period
carry    until ``carryover_expires_at`` zeroed once that instant passes
bought   never                        never
======== ============================== ================================================

This module is pure and side-effect free on purpose: the consumption decision is
money-adjacent and must be unit-testable without a database, a Stripe call, or a
clock. Callers pass ``now`` in explicitly rather than reading the clock here, so
a test can pin an expiry boundary exactly.

Spending order is **carryover first, always**. Carryover is the only population
that can be *lost*, so spending it before anything else is what stops a user's
credits evaporating at a renewal date they never saw. Between the other two the
user chooses (``purchased_credits_first``), because plan credits reset while
purchased credits do not — so the sensible default is to burn the resetting ones
first and hold the paid ones in reserve.
"""

from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "WalletBalances",
    "available_total",
    "consume_from_wallet",
    "live_carryover",
]


@dataclass(frozen=True)
class WalletBalances:
    """A user's spendable credits, split by population.

    Frozen because every consumer treats this as a value: :func:`consume_from_wallet`
    returns a new instance rather than mutating one in place, so a caller holding
    a balance for a response can never see it change underneath them.
    """

    plan_remaining: int
    carryover_credits: int
    purchased_credits: int

    def __post_init__(self) -> None:
        for name, value in (
            ("plan_remaining", self.plan_remaining),
            ("carryover_credits", self.carryover_credits),
            ("purchased_credits", self.purchased_credits),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative (got {value}).")


def live_carryover(
    carryover_credits: int, carryover_expires_at: datetime | None, now: datetime
) -> int:
    """Carryover credits still usable at ``now``.

    Expiry is evaluated here, at read time, rather than by a scheduled job. That
    is deliberate: a cron that zeroes the column can fail, be delayed, or not be
    deployed, whereas a read-time comparison cannot — the credits are simply not
    spendable once the instant passes. The stored number is left alone so the
    expiry is auditable after the fact.

    ``expires_at is None`` means "no expiry recorded", which is treated as
    "does not expire" rather than "expired". Reading a missing timestamp as
    expiry would silently destroy credits, and this module exists precisely to
    avoid that.

    The comparison is ``>=``, so the boundary instant itself is expired: an
    expiry of 12:00 means the credits are gone *at* 12:00, not a second later.
    """
    if carryover_credits <= 0:
        return 0
    if carryover_expires_at is not None and now >= carryover_expires_at:
        return 0
    return carryover_credits


def available_total(
    balances: WalletBalances,
    *,
    carryover_expires_at: datetime | None,
    now: datetime,
) -> int:
    """Everything spendable right now: live carryover + plan + purchased."""
    return (
        balances.plan_remaining
        + live_carryover(balances.carryover_credits, carryover_expires_at, now)
        + balances.purchased_credits
    )


def consume_from_wallet(
    balances: WalletBalances,
    units: int,
    *,
    carryover_expires_at: datetime | None,
    now: datetime,
    purchased_credits_first: bool = False,
) -> WalletBalances:
    """Spend ``units`` across the populations, in order.

    Order: **carryover → (plan or purchased, by preference) → the other.**

    A request for more than the total available spends everything there is and
    returns zeros; it does not raise and it never goes negative. That is the
    existing behaviour of the conversion path (``_consume_once`` clamps at the
    floor), and preserving it matters: raising here would turn "out of credits"
    into a 500 on the worker instead of a refusal the user can act on.

    Spending **zero** is a no-op, not an error. ``Credit.consume_for_conversion``
    raises on ``units <= 0`` and the worker only catches ``InsufficientCredits``,
    so a job whose computed cost is zero would currently crash the consumption
    path; treating zero as "spend nothing" removes that hazard.

    Only the *live* portion of a carryover is spendable, but the result carries
    the **stored** number forward: an expired remainder is left in place rather
    than zeroed, so the expiry stays auditable and :func:`live_carryover` keeps
    deciding. Nothing is spent from an expired bucket.
    """
    if units < 0:
        raise ValueError(f"units cannot be negative (got {units}).")
    if units == 0:
        return balances

    remaining = units

    # 1. Carryover first: the only population that can be lost.
    stored_carryover = balances.carryover_credits
    live = live_carryover(stored_carryover, carryover_expires_at, now)
    spend = min(live, remaining)
    carryover = stored_carryover - spend
    remaining -= spend

    plan = balances.plan_remaining
    purchased = balances.purchased_credits

    # 2. The two non-expiring pools, in the user's preferred order. Written as
    # explicit branches rather than a loop because there are exactly two and an
    # unnamed ordering would be harder to read than the names themselves.
    if purchased_credits_first:
        spend = min(purchased, remaining)
        purchased -= spend
        remaining -= spend

        spend = min(plan, remaining)
        plan -= spend
        remaining -= spend
    else:
        spend = min(plan, remaining)
        plan -= spend
        remaining -= spend

        spend = min(purchased, remaining)
        purchased -= spend
        remaining -= spend

    # `remaining` can only be non-zero when every pool is empty; the result is
    # all zeros in that case, which is the intended clamp.
    return WalletBalances(
        plan_remaining=plan,
        carryover_credits=carryover,
        purchased_credits=purchased,
    )
