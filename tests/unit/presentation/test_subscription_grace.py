"""Regression tests for the subscription payment-grace lifecycle.

These are the tests that must **fail** on the pre-fix code: before this work,
``invoice.payment_failed`` was a log line and ``customer.subscription.updated``
re-applied the paid tier for ``past_due``/``unpaid``, so a customer who stopped
paying kept paid access indefinitely.

They run against a real (in-memory SQLite) schema rather than fakes: the guarded
``UPDATE`` statements are the idempotency mechanism, so testing them with a
hand-written double would prove nothing.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    ensure_utc,
)
from src.domain.subscriptions.value_object.payment_state import PaymentState
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import (
    SQLSubscriptionRepository,
)
from src.infrastructure.database.models import (
    MonthlyCreditModel,
    StripeWebhookEventModel,
    UserSubscriptionModel,
)
from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1 import webhooks

USER_ID = 42
SUBSCRIPTION_ID = "sub_current"
CUSTOMER_ID = "cus_1"

#: Friday 2026-11-06 14:00 UTC. Three business days later is Wednesday the 11th.
FAILED_AT = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)
#: End of that third business day — the inclusive deadline.
EXPECTED_DEADLINE = datetime(2026, 11, 11, 23, 59, 59, 999999, tzinfo=UTC)


@contextmanager
def sqlite_session_factory():
    """Yield an async_sessionmaker bound to a fresh in-memory SQLite DB."""
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _seed_subscription(
    factory,
    *,
    tier: SubscriptionTier = SubscriptionTier.PRO,
    subscription_id: str | None = SUBSCRIPTION_ID,
    payment_state: str = PaymentState.OK.value,
    grace_deadline_at: datetime | None = None,
    last_payment_event_at: datetime | None = None,
    purchased_credits: int = 0,
) -> None:
    async with factory() as session:
        session.add(
            UserSubscriptionModel(
                actor_key=f"user:{USER_ID}",
                user_id=USER_ID,
                tier=tier,
                stripe_customer_id=CUSTOMER_ID,
                stripe_subscription_id=subscription_id,
                stripe_status="active",
                payment_state=payment_state,
                grace_deadline_at=grace_deadline_at,
                last_payment_event_at=last_payment_event_at,
                purchased_credits=purchased_credits,
            )
        )
        await session.commit()


async def _row(factory) -> UserSubscriptionModel:
    async with factory() as session:
        result = await session.execute(
            select(UserSubscriptionModel).where(UserSubscriptionModel.user_id == USER_ID)
        )
        row = result.scalar_one()
        session.expunge(row)
        return row


def _invoice_failed_event(*, created: datetime = FAILED_AT) -> dict:
    return {
        "id": "in_failed_1",
        "subscription": SUBSCRIPTION_ID,
        "customer": CUSTOMER_ID,
        "created": int(created.timestamp()),
        "status": "open",
    }


def _subscription_event(*, status: str, event_time: datetime | None = None) -> dict:
    return {
        "id": SUBSCRIPTION_ID,
        "customer": CUSTOMER_ID,
        "status": status,
        "cancel_at_period_end": False,
        "metadata": {"user_id": str(USER_ID), "tier": "pro", "kind": "subscription"},
        "items": {"data": [{"current_period_end": int(FAILED_AT.timestamp())}]},
    }


# ---------------------------------------------------------------------------
# Grace initiation
# ---------------------------------------------------------------------------


def test_failed_payment_opens_a_three_business_day_grace_window() -> None:
    """The core requirement: a failed renewal starts a bounded clock.

    Fails before the fix: the handler only logged, so no deadline existed and the
    paid tier was never time-bounded.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_invoice_payment_failed(
                    session,
                    _invoice_failed_event(),
                    event_created=FAILED_AT + timedelta(minutes=1),
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.GRACE.value
            assert ensure_utc(row.grace_deadline_at) == EXPECTED_DEADLINE
            # The plan is retained during grace — only the deadline bounds it.
            assert row.tier == SubscriptionTier.PRO
            assert row.entitlement_revoked_at is None

        asyncio.run(_run())


def test_paid_entitlement_survives_inside_the_window_and_ends_at_the_deadline() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_invoice_payment_failed(
                    session, _invoice_failed_event()
                )

            async with factory() as session:
                repo = SQLSubscriptionRepository(session)
                # A frozen "now" is not available to the repository, so assert
                # the boundary through the pure derivation the repository uses.
                row = await _row(factory)
                assert row.tier == SubscriptionTier.PRO

            from src.domain.subscriptions.policies.payment_grace import (
                effective_tier,
            )

            common = dict(
                tier=SubscriptionTier.PRO,
                payment_state=PaymentState.GRACE,
                grace_deadline_at=EXPECTED_DEADLINE,
                stripe_status="past_due",
            )
            assert (
                effective_tier(now=EXPECTED_DEADLINE - timedelta(seconds=1), **common)
                == SubscriptionTier.PRO
            )
            assert effective_tier(now=EXPECTED_DEADLINE, **common) == SubscriptionTier.FREE

        asyncio.run(_run())


def test_duplicate_failure_does_not_extend_the_deadline() -> None:
    """Replaying (or retrying) a failure must never push the window out.

    This is the "grace must not be extendable by webhook replay" requirement.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_invoice_payment_failed(
                    session, _invoice_failed_event()
                )

            original = (await _row(factory)).grace_deadline_at

            # A later duplicate, same invoice, and a *second* failed invoice.
            async with factory() as session:
                await webhooks._handle_invoice_payment_failed(
                    session,
                    _invoice_failed_event(created=FAILED_AT + timedelta(days=1)),
                    event_created=FAILED_AT + timedelta(days=1),
                )
                await webhooks._handle_invoice_payment_failed(
                    session,
                    {**_invoice_failed_event(), "id": "in_failed_2"},
                    event_created=FAILED_AT + timedelta(days=2),
                )

            row = await _row(factory)
            assert ensure_utc(row.grace_deadline_at) == ensure_utc(original)
            assert row.payment_state == PaymentState.GRACE.value

        asyncio.run(_run())


def test_a_failure_on_an_already_free_plan_opens_no_window() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory, tier=SubscriptionTier.FREE)
            async with factory() as session:
                await webhooks._handle_invoice_payment_failed(
                    session, _invoice_failed_event()
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.OK.value
            assert row.grace_deadline_at is None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# customer.subscription.updated — status awareness
# ---------------------------------------------------------------------------


def test_past_due_does_not_regrant_the_plan_or_its_credits() -> None:
    """The event that signals non-payment must not re-grant entitlement.

    Fails before the fix: every status except ``canceled`` fell through to
    ``_activate_subscription``, which reset the tier and could top up the plan
    bucket.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="past_due"),
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.GRACE.value
            assert row.grace_deadline_at is not None

            async with factory() as session:
                # No plan bucket was created by a *failed* payment.
                credit = await SQLCreditRepository(session).get_credit(
                    str(USER_ID), current_period_key()
                )
                assert credit is None

        asyncio.run(_run())


def test_unpaid_keeps_the_plan_only_until_the_deadline() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="unpaid"),
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.GRACE.value
            assert row.tier == SubscriptionTier.PRO
            assert ensure_utc(row.grace_deadline_at) is not None

        asyncio.run(_run())


def test_incomplete_expired_never_grants_a_paid_tier() -> None:
    """An abandoned checkout must not activate a plan.

    ``incomplete_expired`` previously took the ``else`` branch and activated the
    metadata tier for a payment that never happened.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="incomplete_expired"),
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.tier == SubscriptionTier.FREE
            assert row.payment_state == PaymentState.OK.value
            assert row.grace_deadline_at is None

        asyncio.run(_run())


def test_active_payment_clears_the_grace_window() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=EXPECTED_DEADLINE,
                last_payment_event_at=FAILED_AT,
            )
            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="active"),
                    event_created=FAILED_AT + timedelta(hours=1),
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.OK.value
            assert row.grace_deadline_at is None
            assert row.tier == SubscriptionTier.PRO
            assert row.stripe_status == "active"

        asyncio.run(_run())


def test_a_stale_success_cannot_clear_a_newer_failure() -> None:
    """Out-of-order delivery must not resurrect entitlement.

    The stored ``last_payment_event_at`` is newer than the incoming event, so the
    transition is rejected by the ordering guard.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            newer_failure = FAILED_AT + timedelta(hours=2)
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=EXPECTED_DEADLINE,
                last_payment_event_at=newer_failure,
            )
            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="active"),
                    event_created=FAILED_AT,  # older than the stored failure
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.GRACE.value
            assert ensure_utc(row.grace_deadline_at) == EXPECTED_DEADLINE

        asyncio.run(_run())


def test_deletion_of_a_superseded_subscription_is_ignored() -> None:
    """A late cancellation for an old subscription must not downgrade a payer."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_subscription_deleted(
                    session,
                    {
                        "id": "sub_old",
                        "customer": CUSTOMER_ID,
                        "metadata": {"user_id": str(USER_ID)},
                    },
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.tier == SubscriptionTier.PRO

        asyncio.run(_run())


def test_deletion_of_the_current_subscription_downgrades() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory)
            async with factory() as session:
                await webhooks._handle_subscription_deleted(
                    session,
                    {
                        "id": SUBSCRIPTION_ID,
                        "customer": CUSTOMER_ID,
                        "metadata": {"user_id": str(USER_ID)},
                    },
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.tier == SubscriptionTier.FREE
            assert row.grace_deadline_at is None
            # Retained deliberately: recovery and reconciliation need it.
            assert row.stripe_subscription_id == SUBSCRIPTION_ID

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Enforcement without the worker
# ---------------------------------------------------------------------------


def test_entitlement_expires_on_read_even_if_the_sweeper_never_runs() -> None:
    """The maximum is enforced by the read path, not by the background job.

    If enforcement depended on the sweep, a stopped worker (or a server that was
    simply switched off) would make "three business days" unbounded.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=datetime.now(UTC) - timedelta(minutes=1),
            )

            async with factory() as session:
                tier = await SQLSubscriptionRepository(session).get_tier_for_user(USER_ID)
            assert tier == SubscriptionTier.FREE

            # The stored plan is untouched: only entitlement is derived, so a
            # later payment can restore it without guessing.
            row = await _row(factory)
            assert row.tier == SubscriptionTier.PRO

        asyncio.run(_run())


def test_grace_before_the_deadline_still_yields_the_paid_tier_on_read() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=datetime.now(UTC) + timedelta(days=1),
            )
            async with factory() as session:
                tier = await SQLSubscriptionRepository(session).get_tier_for_user(USER_ID)
            assert tier == SubscriptionTier.PRO

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Sweeper (the persistence half)
# ---------------------------------------------------------------------------


def test_sweep_revokes_only_expired_windows_and_clamps_the_plan_bucket() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            now = datetime.now(UTC)
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=now - timedelta(minutes=1),
                purchased_credits=1000,
            )
            async with factory() as session:
                session.add(
                    MonthlyCreditModel(
                        owner_id=str(USER_ID),
                        period_key=current_period_key(now),
                        allowance=500,
                        remaining=320,
                    )
                )
                await session.commit()

            async with factory() as session:
                repo = SQLSubscriptionRepository(session)
                due = await repo.get_due_grace_rows(now=now)
                assert len(due) == 1
                await repo.revoke_entitlement(due[0], now=now)
                await session.commit()

            row = await _row(factory)
            assert row.tier == SubscriptionTier.FREE
            assert row.payment_state == PaymentState.REVOKED.value
            # Remembered so a later payment restores what was taken away.
            assert row.previous_paid_tier == SubscriptionTier.PRO.value
            assert row.entitlement_revoked_at is not None
            # Purchased credits are money already spent; they survive.
            assert row.purchased_credits == 1000

            async with factory() as session:
                credit = await SQLCreditRepository(session).get_credit(
                    str(USER_ID), current_period_key(now)
                )
                assert credit is not None
                # The plan grant is part of the paid entitlement.
                assert credit.allowance == 50
                assert credit.remaining == 50

        asyncio.run(_run())


def test_sweep_ignores_a_window_that_has_not_closed() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            now = datetime.now(UTC)
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=now + timedelta(days=1),
            )
            async with factory() as session:
                repo = SQLSubscriptionRepository(session)
                assert await repo.get_due_grace_rows(now=now) == []

            row = await _row(factory)
            assert row.tier == SubscriptionTier.PRO

        asyncio.run(_run())


def test_revocation_is_idempotent() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            now = datetime.now(UTC)
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=now - timedelta(minutes=1),
            )
            async with factory() as session:
                repo = SQLSubscriptionRepository(session)
                due = await repo.get_due_grace_rows(now=now)
                await repo.revoke_entitlement(due[0], now=now)
                await session.commit()

            async with factory() as session:
                repo = SQLSubscriptionRepository(session)
                # Already REVOKED, so it is no longer selectable as due.
                assert await repo.get_due_grace_rows(now=now) == []

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Event ledger
# ---------------------------------------------------------------------------


def test_webhook_event_is_deduplicated_by_stripe_event_id() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                first = await webhooks._claim_event(
                    session,
                    event_id="evt_1",
                    event_type="invoice.payment_failed",
                    event_created=1,
                )
                assert first is True
                await webhooks._record_event_outcome(
                    session, event_id="evt_1", outcome="processed"
                )

                duplicate = await webhooks._claim_event(
                    session,
                    event_id="evt_1",
                    event_type="invoice.payment_failed",
                    event_created=1,
                )
                assert duplicate is False

                stored = await session.get(StripeWebhookEventModel, "evt_1")
                assert stored is not None
                assert stored.outcome == "processed"

        asyncio.run(_run())


def test_a_failed_event_may_be_reprocessed_by_a_retry() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                assert await webhooks._claim_event(
                    session, event_id="evt_2", event_type="invoice.payment_succeeded", event_created=1
                )
                await webhooks._record_event_outcome(
                    session, event_id="evt_2", outcome="failed", error="boom"
                )

                # Stripe retries after a non-2xx; that retry must be allowed.
                assert await webhooks._claim_event(
                    session, event_id="evt_2", event_type="invoice.payment_succeeded", event_created=1
                )

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def test_recovery_after_revocation_restores_the_plan() -> None:
    """Paying after the window closed must restore what was taken away."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                session.add(
                    UserSubscriptionModel(
                        actor_key=f"user:{USER_ID}",
                        user_id=USER_ID,
                        tier=SubscriptionTier.FREE,
                        stripe_customer_id=CUSTOMER_ID,
                        stripe_subscription_id=SUBSCRIPTION_ID,
                        stripe_status="past_due",
                        payment_state=PaymentState.REVOKED.value,
                        grace_deadline_at=FAILED_AT + timedelta(days=3),
                        last_payment_event_at=FAILED_AT,
                        previous_paid_tier=SubscriptionTier.PRO.value,
                        entitlement_revoked_at=FAILED_AT + timedelta(days=4),
                        purchased_credits=1000,
                    )
                )
                await session.commit()

            async with factory() as session:
                await webhooks._handle_subscription_updated(
                    session,
                    _subscription_event(status="active"),
                    event_created=FAILED_AT + timedelta(days=5),
                )

            row = await _row(factory)
            assert row.tier == SubscriptionTier.PRO
            assert row.payment_state == PaymentState.OK.value
            assert row.grace_deadline_at is None
            assert row.entitlement_revoked_at is None
            assert row.previous_paid_tier is None
            # Bought, never expiring — untouched by revocation and recovery.
            assert row.purchased_credits == 1000

        asyncio.run(_run())


def test_a_paid_invoice_does_not_clear_grace_while_another_is_still_unpaid(
    monkeypatch,
) -> None:
    """A $0/proration invoice settling must not end the window.

    The subscription's *status* is authoritative: if Stripe still reports
    ``past_due``, another invoice is outstanding and entitlement stays bounded.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(
                factory,
                payment_state=PaymentState.GRACE.value,
                grace_deadline_at=EXPECTED_DEADLINE,
                last_payment_event_at=FAILED_AT,
            )

            async def _snapshot(_subscription_id: str) -> dict:
                return {
                    "id": SUBSCRIPTION_ID,
                    "status": "past_due",
                    "metadata": {"user_id": str(USER_ID), "tier": "pro"},
                    "customer": CUSTOMER_ID,
                    "cancel_at_period_end": False,
                    "current_period_end": None,
                    "price_id": None,
                }

            monkeypatch.setattr(webhooks, "_stripe_subscription_snapshot", _snapshot)

            async with factory() as session:
                await webhooks._refresh_allowance_for_invoice(
                    session,
                    {"id": "in_zero", "subscription": SUBSCRIPTION_ID},
                    event_created=FAILED_AT + timedelta(hours=1),
                )

            row = await _row(factory)
            assert row.payment_state == PaymentState.GRACE.value
            assert ensure_utc(row.grace_deadline_at) == EXPECTED_DEADLINE

        asyncio.run(_run())


def test_a_paid_invoice_on_a_cancelled_subscription_does_not_resurrect_it(
    monkeypatch,
) -> None:
    """Out-of-order delivery must not restore a subscription Stripe has ended."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            await _seed_subscription(factory, tier=SubscriptionTier.FREE)

            async def _snapshot(_subscription_id: str) -> dict:
                # Metadata still names the old plan — exactly what the old code
                # trusted when it re-activated the subscription.
                return {
                    "id": SUBSCRIPTION_ID,
                    "status": "canceled",
                    "metadata": {"user_id": str(USER_ID), "tier": "pro_plus"},
                    "customer": CUSTOMER_ID,
                    "cancel_at_period_end": False,
                    "current_period_end": None,
                    "price_id": None,
                }

            monkeypatch.setattr(webhooks, "_stripe_subscription_snapshot", _snapshot)

            async with factory() as session:
                await webhooks._refresh_allowance_for_invoice(
                    session,
                    {"id": "in_final", "subscription": SUBSCRIPTION_ID},
                    event_created=FAILED_AT,
                )

            row = await _row(factory)
            assert row.tier == SubscriptionTier.FREE

        asyncio.run(_run())

