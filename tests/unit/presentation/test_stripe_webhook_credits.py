"""Unit tests for the Stripe webhook credit-grant logic."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1 import webhooks
from src.presentation.schemas.credit import TransactionType

# Purchased credits live in their own wallet column; they are never merged into
# the plan bucket, so a purchase must not create one.
PURCHASED = 100


def _period_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


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


def test_grant_purchased_credits_goes_to_the_purchased_wallet() -> None:
    """Purchased credits belong to the wallet column, not the plan bucket."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                await webhooks._grant_purchased_credits(
                    session,
                    user_id="42",
                    credits=PURCHASED,
                    reference_id="cs_mock_1",
                )

                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                # The plan bucket is untouched: a purchase is not a plan grant.
                credit = await SQLCreditRepository(session).get_credit("42", _period_key())
                assert credit is None

                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.purchased_credits == PURCHASED

                txns = await SQLCreditRepository(session).list_transactions(42, offset=0, limit=10)
                assert len(txns) == 1
                assert txns[0].transaction_type == TransactionType.PURCHASE.value
                assert txns[0].reference_id == "cs_mock_1"

        asyncio.run(_run())


def test_grant_purchased_credits_is_idempotent_by_reference() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                for _ in range(2):
                    await webhooks._grant_purchased_credits(
                        session,
                        user_id="42",
                        credits=PURCHASED,
                        reference_id="cs_mock_1",
                    )

                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                # Credited once, not twice.
                assert row.purchased_credits == PURCHASED

                txns = await SQLCreditRepository(session).list_transactions(42, offset=0, limit=10)
                assert len(txns) == 1

        asyncio.run(_run())


def test_grant_purchased_credits_rolls_back_the_wallet_on_a_unique_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The duplicate path must undo the wallet bump, not just skip the ledger.

    ``save_credit`` used to commit the bump before the unique-constraint check,
    so the ``rollback`` on a duplicate could not undo it and a concurrent
    delivery would double-credit the user. The bump and the ledger insert now
    share one transaction. The pre-check is stubbed out to force the
    constraint (rather than the pre-check) to be the guard under test.
    """
    from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository

    async def _no_transactions(*args, **kwargs):
        return []

    monkeypatch.setattr(SQLCreditRepository, "list_transactions", _no_transactions)

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                await webhooks._grant_purchased_credits(
                    session, user_id="42", credits=PURCHASED, reference_id="cs_race"
                )
                # The pre-check is blind, so this second call reaches the insert
                # and is rejected by the unique constraint instead.
                await webhooks._grant_purchased_credits(
                    session, user_id="42", credits=PURCHASED, reference_id="cs_race"
                )

                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.purchased_credits == PURCHASED  # not doubled

                # ``list_transactions`` is blind in this test, so count via a
                # direct query through a session bound to the real repository
                # method would hit the stub; assert on the wallet instead and
                # rely on the unique constraint for the ledger.
        asyncio.run(_run())


def test_checkout_completed_skips_unpaid_sessions() -> None:
    """Delayed-notification sessions arriving as 'unpaid' must not fulfill."""
    async def _run() -> None:
        event = {
            "id": "cs_mock_unpaid",
            "payment_status": "unpaid",
            "metadata": {"user_id": "42", "kind": "credit_purchase", "credits": "100"},
        }
        # No DB interaction expected; use a sentinel that would blow up if touched.
        # ``None`` is fine because the unpaid branch returns before touching ``db``.
        await webhooks._handle_checkout_completed(None, event)  # type: ignore[arg-type]

    asyncio.run(_run())


def test_checkout_completed_grant_credit_purchase() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            event = {
                "id": "cs_mock_paid",
                "payment_status": "paid",
                "customer": "cus_mock",
                "subscription": None,
                "metadata": {"user_id": "42", "kind": "credit_purchase", "credits": "100"},
            }
            async with factory() as session:
                await webhooks._handle_checkout_completed(session, event)

                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.purchased_credits == 100

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Re-applying a plan must NEVER destroy purchased credits.
#
# Purchased credits share the bucket with the plan grant, so after a purchase the
# stored allowance is LARGER than the tier's monthly grant. The old code clamped
# both fields down to the tier grant in that case, which wiped the purchase:
# 500 plan + 1000 bought = 1500, and re-applying PRO dropped it to 500.
#
# This is not a corner case. Stripe retries webhook deliveries, and
# ``invoice.payment_succeeded`` re-applies the tier on every monthly renewal, so
# the bug would silently destroy purchased credits once a month.
# ---------------------------------------------------------------------------

PLAN_PRO = 500  # TierPolicy(PRO).monthly_conversion_credits
PURCHASED_CREDITS = 1000


def test_purchased_credits_survive_subscription_reactivation() -> None:
    """The exact production failure: bought credits must survive a re-apply.

    A purchase now lands in its own column, so the only thing re-applying a tier
    can touch is the plan bucket. Both the plan grant and the purchased total
    must be unchanged after a Stripe retry / monthly renewal.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                # Arrange: activate PRO, then buy 1000 credits on top.
                await webhooks._activate_subscription(
                    session,
                    user_id="42",
                    tier_name="pro",
                    stripe_customer_id="cus_mock",
                    stripe_subscription_id="sub_mock",
                )
                await webhooks._grant_purchased_credits(
                    session, user_id="42", credits=PURCHASED_CREDITS, reference_id="cs_buy_1"
                )

                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                repo = SQLCreditRepository(session)
                sub_repo = SQLSubscriptionRepository(session)
                before_plan = await repo.get_credit("42", _period_key())
                before_wallet = await sub_repo.get_wallet(42)
                assert before_plan is not None
                assert before_plan.allowance == PLAN_PRO
                assert before_wallet is not None
                assert before_wallet.purchased_credits == PURCHASED_CREDITS

                # Act: re-apply the SAME plan — a Stripe retry, or the
                # invoice.payment_succeeded sent on the monthly renewal.
                await webhooks._activate_subscription(
                    session,
                    user_id="42",
                    tier_name="pro",
                    stripe_customer_id="cus_mock",
                    stripe_subscription_id="sub_mock",
                )

                # Assert: the purchased credits and the plan bucket are intact.
                after_plan = await repo.get_credit("42", _period_key())
                after_wallet = await sub_repo.get_wallet(42)
                assert after_plan is not None
                assert after_plan.allowance == PLAN_PRO
                assert after_plan.remaining == before_plan.remaining
                assert after_wallet is not None
                assert after_wallet.purchased_credits == PURCHASED_CREDITS

        asyncio.run(_run())


def test_reactivating_a_plan_is_idempotent() -> None:
    """Applying the same tier repeatedly must not change the bucket."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository

                for _ in range(3):
                    await webhooks._activate_subscription(
                        session,
                        user_id="7",
                        tier_name="pro",
                        stripe_customer_id="cus_x",
                        stripe_subscription_id="sub_x",
                    )

                repo = SQLCreditRepository(session)
                credit = await repo.get_credit("7", _period_key())
                assert credit is not None
                assert credit.allowance == PLAN_PRO
                assert credit.remaining == PLAN_PRO

        asyncio.run(_run())


def test_applying_a_smaller_plan_does_not_claw_back_purchased_credits() -> None:
    """A downgrade must not confiscate credits the user paid for.

    Purchased credits are a separate column now, so re-applying a smaller tier
    has no code path that could reach them; this pins that the wallet column is
    untouched and the plan bucket is not reduced by the webhook.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
                from src.infrastructure.adapters.repository.sql_subscription_repo import (
                    SQLSubscriptionRepository,
                )

                await webhooks._activate_subscription(
                    session,
                    user_id="9",
                    tier_name="pro_plus",
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                )
                await webhooks._grant_purchased_credits(
                    session, user_id="9", credits=PURCHASED_CREDITS, reference_id="cs_buy_2"
                )
                repo = SQLCreditRepository(session)
                sub_repo = SQLSubscriptionRepository(session)
                before = await repo.get_credit("9", _period_key())
                assert before is not None

                # Downgrade to the cheaper plan.
                await webhooks._activate_subscription(
                    session,
                    user_id="9",
                    tier_name="pro",
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                )

                after = await repo.get_credit("9", _period_key())
                after_wallet = await sub_repo.get_wallet(9)
                assert after is not None
                # The plan bucket is never reduced by the webhook, and the paid
                # credits are untouched in their own column.
                assert after.remaining == before.remaining
                assert after.remaining <= after.allowance
                assert after_wallet is not None
                assert after_wallet.purchased_credits == PURCHASED_CREDITS

        asyncio.run(_run())


def test_upgrading_a_plan_raises_the_allowance_and_keeps_unspent_credits() -> None:
    """The legitimate case must still work: raise the grant, carry the balance."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository

                # FREE bucket at 50, with some already spent.
                repo = SQLCreditRepository(session)
                from src.domain.subscriptions.entities.credit import Credit
                from src.domain.subscriptions.value_object.tier import SubscriptionTier
                await repo.save_credit(
                    Credit.from_tier(owner_id="11", period_key=_period_key(), tier=SubscriptionTier.FREE)
                )
                seeded = await repo.get_credit("11", _period_key())
                assert seeded is not None
                seeded.remaining = 20
                await repo.save_credit(seeded)

                await webhooks._activate_subscription(
                    session,
                    user_id="11",
                    tier_name="pro",
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                )

                upgraded = await repo.get_credit("11", _period_key())
                assert upgraded is not None
                assert upgraded.allowance == PLAN_PRO
                # 20 unspent + (500 - 50) added by the upgrade.
                assert upgraded.remaining == 20 + (PLAN_PRO - 50)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# SEC-6 — unbounded body read on the unauthenticated webhook
# ---------------------------------------------------------------------------

def _configured_webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(STRIPE_WEBHOOK_SECRET=SecretStr("whsec_test")),
    )


def test_webhook_rejects_oversized_body_before_reading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured_webhook_secret(monkeypatch)

    class OversizedRequest:
        headers = {"content-length": str(2 * 1024 * 1024)}

        async def body(self) -> bytes:
            raise AssertionError("the body must not be read for an oversized payload")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webhooks.handle_stripe_webhook(OversizedRequest(), db=None))  # type: ignore[arg-type]

    assert exc.value.status_code == 413


def test_webhook_without_content_length_is_not_pre_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunked clients send no Content-Length and must reach signature checks."""
    _configured_webhook_secret(monkeypatch)

    class ChunkedRequest:
        headers: dict[str, str] = {}

        async def body(self) -> bytes:
            return b"{}"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webhooks.handle_stripe_webhook(ChunkedRequest(), db=None))  # type: ignore[arg-type]

    # Fails signature verification (400), i.e. it was not blocked by the size gate.
    assert exc.value.status_code == 400
