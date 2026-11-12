"""Payment-recovery state for a subscription.

This is a deliberately **separate axis** from :class:`SubscriptionTier` and from
the Stripe status mirror. Three different questions used to be answered by the
single ``user_subscriptions.tier`` column:

1. *What plan did the customer buy?* → ``tier``
2. *Is that plan currently paid for?* → :class:`PaymentState`
3. *What did Stripe last tell us?* → ``stripe_status``

Conflating them is what made "keep the tier forever" possible: a lapsed
subscription had nowhere to record that it had lapsed, so every gate kept
reading the purchased plan as if it were an entitlement.

Values are plain strings (not a PostgreSQL enum) for the same reason migration
``0021_job_origin`` used a string: ``ALTER TYPE ... ADD VALUE`` cannot be used in
the transaction that reads the new value, so a new state would need a migration
that must commit before the code can use it. A string column grows a state
without a schema change.
"""

from enum import StrEnum


class PaymentState(StrEnum):
    """Where a subscription is in the payment-recovery lifecycle.

    ``OK``
        Paid and current. Entitlement is whatever ``tier`` says.

    ``GRACE``
        A recurring payment failed and the customer is inside the recovery
        window. The paid entitlement is **retained** until ``grace_deadline_at``.
        This is the only state in which entitlement is time-bounded.

    ``REVOKED``
        The grace window closed without payment. Paid entitlement is removed
        locally (``tier`` becomes ``FREE``). This says **nothing** about Stripe:
        the subscription is *not* cancelled by revocation, and
        ``stripe_subscription_id`` is deliberately retained so a later payment
        can restore the plan.
    """

    OK = "OK"
    GRACE = "GRACE"
    REVOKED = "REVOKED"


#: Stripe subscription statuses that represent a subscription which has paid for
#: the period it is currently in. Only these may grant a paid entitlement.
#:
#: ``trialing`` is included because a trial is a legitimate, Stripe-authorised
#: period of paid access — it simply has not been charged yet.
ENTITLED_STRIPE_STATUSES: frozenset[str] = frozenset({"active", "trialing"})

#: Statuses that mean "a payment has failed / is outstanding". These open (or
#: continue) the grace window and never grant a *new* entitlement.
#:
#: ``past_due`` — the renewal invoice failed and Stripe is retrying.
#: ``unpaid``   — retries are exhausted; Stripe has stopped trying.
#: ``paused``   — collection is paused, so no payment is being attempted.
PAYMENT_FAILURE_STRIPE_STATUSES: frozenset[str] = frozenset(
    {"past_due", "unpaid", "paused"}
)

#: Statuses that are terminal or represent a checkout that never completed.
#: These must never grant a paid entitlement, and they end any grace window.
#:
#: ``canceled``            — the subscription has ended.
#: ``incomplete``          — the first payment has not been confirmed yet.
#: ``incomplete_expired``  — the first payment was never completed and Stripe
#:                           has given up. Historically this *granted* a paid
#:                           tier, which handed free access to anyone who
#:                           abandoned checkout.
NON_ENTITLING_STRIPE_STATUSES: frozenset[str] = frozenset(
    {"canceled", "incomplete", "incomplete_expired"}
)


def opens_grace(status: str | None) -> bool:
    """Return True when a Stripe status means a payment failure.

    Unknown or absent statuses return False: this function must never invent a
    grace period from missing information.
    """
    return (status or "").lower() in PAYMENT_FAILURE_STRIPE_STATUSES


def is_entitling(status: str | None) -> bool:
    """Return True when a Stripe status may grant paid entitlement."""
    return (status or "").lower() in ENTITLED_STRIPE_STATUSES


def is_terminal(status: str | None) -> bool:
    """Return True when a Stripe status ends the subscription for good."""
    return (status or "").lower() in NON_ENTITLING_STRIPE_STATUSES
