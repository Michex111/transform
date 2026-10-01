"""``POST /api/v1/subscription/change-plan``.

Called directly with a real (SQLite) session and a fake Stripe service, matching
the style of the webhook credit tests. The endpoint's job is the wallet
arithmetic: an upgrade must move the unspent plan balance into expiring
carryover, reset the plan bucket to the new grant, and never route through
checkout.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    ensure_utc,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.infrastructure.adapters.payment.stripe_service import PlanChangeResult
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.database.models import UserSubscriptionModel
from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1.subscriptions import change_plan
from src.presentation.schemas.credit import TransactionType
from src.presentation.schemas.subscription import ChangePlanRequest, SubscriptionTier

OLD_PERIOD_END = datetime(2026, 11, 1, tzinfo=UTC)


@contextmanager
def sqlite_session_factory():
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
    user_id: int,
    tier: DomainTier,
    *,
    stripe_subscription_id: str | None = "sub_1",
    carryover: int = 0,
    purchased: int = 0,
) -> None:
    async with factory() as session:
        session.add(
            UserSubscriptionModel(
                actor_key=f"user:{user_id}",
                user_id=user_id,
                tier=tier,
                used_storage_bytes=0,
                stripe_subscription_id=stripe_subscription_id,
                carryover_credits=carryover,
                purchased_credits=purchased,
            )
        )
        await session.commit()


async def _seed_bucket(factory, user_id: int, allowance: int, remaining: int) -> None:
    async with factory() as session:
        await SQLCreditRepository(session=session).save_credit(
            Credit(
                owner_id=str(user_id),
                period_key=current_period_key(),
                allowance=allowance,
                remaining=remaining,
            )
        )


class FakeStripeService:
    """Records the plan-change request; deliberately has no checkout method."""

    enabled = True

    def __init__(self, previous_period_end: datetime = OLD_PERIOD_END) -> None:
        self.previous_period_end = previous_period_end
        self.calls: list[dict[str, Any]] = []

    def resolve_price_id(self, tier: str) -> str | None:
        if tier == "enterprise":
            return None
        return f"price_{tier}"

    async def change_subscription_plan(
        self,
        subscription_id: str,
        *,
        new_price_id: str,
        user_id: str,
        tier: str,
        is_upgrade: bool,
    ) -> PlanChangeResult:
        self.calls.append(
            {
                "subscription_id": subscription_id,
                "new_price_id": new_price_id,
                "user_id": user_id,
                "tier": tier,
                "is_upgrade": is_upgrade,
            }
        )
        return PlanChangeResult(
            subscription_id=subscription_id,
            is_upgrade=is_upgrade,
            previous_period_end=self.previous_period_end,
            scheduled_effective_at=None if is_upgrade else self.previous_period_end,
        )


def _user(user_id: int = 42) -> Any:
    return SimpleNamespace(id=user_id, email="user@example.com")


def _call(factory, payload: ChangePlanRequest, stripe: FakeStripeService, user_id: int = 42):
    async def _run():
        async with factory() as session:
            return await change_plan(
                payload,
                _user(user_id),
                SQLSubscriptionRepository(session),
                SQLCreditRepository(session),
                stripe,  # type: ignore[arg-type]
            )

    return asyncio.run(_run())


def test_rejects_a_no_op_plan_change() -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        with pytest.raises(HTTPException) as exc:
            _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO), stripe)

        assert exc.value.status_code == 400
        assert stripe.calls == []


def test_rejects_enterprise_as_not_self_serve() -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        with pytest.raises(HTTPException) as exc:
            _call(factory, ChangePlanRequest(tier=SubscriptionTier.ENTERPRISE), stripe)

        assert exc.value.status_code == 400
        assert stripe.calls == []


def test_rejects_a_user_without_a_subscription_to_change() -> None:
    with sqlite_session_factory() as factory:
        stripe = FakeStripeService()

        with pytest.raises(HTTPException) as exc:
            _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO), stripe)

        assert exc.value.status_code == 409
        assert stripe.calls == []


def test_upgrade_moves_unspent_plan_credits_into_expiring_carryover() -> None:
    """Pro 320 left -> Pro Plus must be 2000 plan + 320 carryover = 2320."""
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO)
            await _seed_bucket(factory, 42, allowance=500, remaining=320)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        response = _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO_PLUS), stripe)

        assert stripe.calls == [
            {
                "subscription_id": "sub_1",
                "new_price_id": "price_pro_plus",
                "user_id": "42",
                "tier": "pro_plus",
                "is_upgrade": True,
            }
        ]
        assert response.tier == SubscriptionTier.PRO_PLUS
        assert response.previous_tier == SubscriptionTier.PRO
        assert response.plan_credits == 2000
        assert response.carryover_credits == 320
        assert response.carryover_expires_at == OLD_PERIOD_END

        async def _assert_persisted() -> None:
            async with factory() as session:
                sub_repo = SQLSubscriptionRepository(session)
                row = await sub_repo.get_wallet(42)
                assert row is not None
                assert row.tier == DomainTier.PRO_PLUS
                assert row.carryover_credits == 320
                assert ensure_utc(row.carryover_expires_at) == OLD_PERIOD_END

                credit = await SQLCreditRepository(session).get_credit(
                    "42", current_period_key()
                )
                assert credit is not None
                assert credit.allowance == 2000
                assert credit.remaining == 2000

                txns = await SQLCreditRepository(session).list_transactions(42, 0, 10)
                assert len(txns) == 1
                assert txns[0].transaction_type == TransactionType.CARRYOVER.value
                assert txns[0].amount == 320

        asyncio.run(_assert_persisted())


def test_upgrade_accumulates_onto_existing_carryover() -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO, carryover=10)
            await _seed_bucket(factory, 42, allowance=500, remaining=320)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        response = _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO_PLUS), stripe)

        assert response.carryover_credits == 330  # 10 existing + 320 new


def test_upgrade_does_not_touch_purchased_credits() -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO, purchased=1000)
            await _seed_bucket(factory, 42, allowance=500, remaining=100)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO_PLUS), stripe)

        async def _assert() -> None:
            async with factory() as session:
                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.purchased_credits == 1000
                assert row.carryover_credits == 100

        asyncio.run(_assert())


def test_downgrade_is_scheduled_without_touching_the_wallet_now() -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO_PLUS)
            await _seed_bucket(factory, 42, allowance=2000, remaining=1500)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        response = _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO), stripe)

        assert stripe.calls[0]["is_upgrade"] is False
        assert response.tier == SubscriptionTier.PRO
        assert response.scheduled_effective_at == OLD_PERIOD_END
        assert response.carryover_credits == 0

        async def _assert() -> None:
            async with factory() as session:
                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                # Tier and wallet are unchanged until the scheduled phase lands.
                assert row.tier == DomainTier.PRO_PLUS
                assert row.carryover_credits == 0
                credit = await SQLCreditRepository(session).get_credit(
                    "42", current_period_key()
                )
                assert credit is not None
                assert credit.remaining == 1500

        asyncio.run(_assert())


# ---------------------------------------------------------------------------
# GAP 2 — the upgrade is one transaction.
#
# Three separately-committing writes used to mean a failure between them could
# leave carryover credited while the plan bucket was never reset (credits
# created) or the tier switched while the wallet was not. The repositories now
# take ``commit=False`` and the route commits once; this proves the rollback on
# a mid-sequence failure leaves NO partial wallet behind.
# ---------------------------------------------------------------------------


def test_a_failure_mid_upgrade_leaves_no_partial_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with sqlite_session_factory() as factory:

        async def _seed() -> None:
            await _seed_subscription(factory, 42, DomainTier.PRO)
            await _seed_bucket(factory, 42, allowance=500, remaining=320)

        asyncio.run(_seed())
        stripe = FakeStripeService()

        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ledger write failed")

        # Fail on the LAST of the three writes, after the wallet and the plan
        # bucket have already been staged in the session.
        monkeypatch.setattr(SQLCreditRepository, "record_transaction", _boom)

        with pytest.raises(RuntimeError):
            _call(factory, ChangePlanRequest(tier=SubscriptionTier.PRO_PLUS), stripe)

        # The Stripe call happened before the local mutation; the rollback must
        # not have undone it, but it must not have "half-applied" locally either.
        assert stripe.calls != []

        async def _assert() -> None:
            async with factory() as session:
                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.tier == DomainTier.PRO  # tier not switched
                assert row.carryover_credits == 0  # nothing credited
                credit = await SQLCreditRepository(session).get_credit(
                    "42", current_period_key()
                )
                assert credit is not None
                assert credit.allowance == 500  # plan bucket not reset
                assert credit.remaining == 320

        asyncio.run(_assert())
