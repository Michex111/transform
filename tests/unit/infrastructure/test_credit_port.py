"""Unit tests for the worker's CreditPort implementation (WorkerCreditRepository)."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import workers.converter_workers.dependencies as dependencies
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.database.models import UserModel, UserSubscriptionModel
from src.infrastructure.database.session import Base
from workers.converter_workers.dependencies import WorkerCreditRepository


@contextmanager
def sqlite_session_factory():
    """Yields an async_sessionmaker bound to a fresh in-memory SQLite DB."""

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


async def _create_user(factory) -> UserModel:
    async with factory() as session:
        user = UserModel(
            username="credit-user",
            email="credit@example.com",
            hashed_password="x",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _set_tier(factory, user_id: int, tier: SubscriptionTier) -> None:
    async with factory() as session:
        session.add(
            UserSubscriptionModel(
                actor_key=f"user:{user_id}",
                user_id=user_id,
                tier=tier,
                used_storage_bytes=0,
            )
        )
        await session.commit()


async def _seed_bucket(
    factory, user_id: int, period_key: str, allowance: int, remaining: int
) -> None:
    async with factory() as session:
        await SQLCreditRepository(session=session).save_credit(
            Credit(
                owner_id=str(user_id),
                period_key=period_key,
                allowance=allowance,
                remaining=remaining,
            )
        )


async def _seed_wallet(
    factory,
    user_id: int,
    *,
    tier: SubscriptionTier = SubscriptionTier.PRO,
    carryover: int = 0,
    expires_at: datetime | None = None,
    purchased: int = 0,
    purchased_first: bool = False,
) -> None:
    """Create the subscription row that carries the non-plan wallet pools."""
    async with factory() as session:
        session.add(
            UserSubscriptionModel(
                actor_key=f"user:{user_id}",
                user_id=user_id,
                tier=tier,
                used_storage_bytes=0,
                carryover_credits=carryover,
                carryover_expires_at=expires_at,
                purchased_credits=purchased,
                purchased_credits_first=purchased_first,
            )
        )
        await session.commit()


async def _wallet_state(
    factory, user_id: int, period_key: str
) -> tuple[int | None, int | None, int | None]:
    """(plan remaining, stored carryover, purchased) as persisted."""
    async with factory() as session:
        credit = await SQLCreditRepository(session=session).get_credit(str(user_id), period_key)
        row = await SQLSubscriptionRepository(session=session).get_wallet(user_id)
        return (
            credit.remaining if credit is not None else None,
            row.carryover_credits if row is not None else None,
            row.purchased_credits if row is not None else None,
        )


def test_get_remaining_returns_none_when_no_bucket() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            port = WorkerCreditRepository(session_factory=factory)
            remaining = await port.get_remaining(user.id, "2026-08")
            assert remaining is None

        asyncio.run(_run())


def test_get_remaining_returns_persisted_value() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_bucket(factory, user.id, "2026-08", 50, 23)
            port = WorkerCreditRepository(session_factory=factory)
            remaining = await port.get_remaining(user.id, "2026-08")
            assert remaining == 23

        asyncio.run(_run())


def test_get_tier_defaults_to_free() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            port = WorkerCreditRepository(session_factory=factory)
            assert await port.get_tier(user.id) == SubscriptionTier.FREE

        asyncio.run(_run())


def test_get_tier_resolves_premium() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _set_tier(factory, user.id, SubscriptionTier.PREMIUM)
            port = WorkerCreditRepository(session_factory=factory)
            assert await port.get_tier(user.id) == SubscriptionTier.PREMIUM

        asyncio.run(_run())


def test_consume_initializes_from_tier_allowance_and_deducts() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _set_tier(factory, user.id, SubscriptionTier.PREMIUM)
            port = WorkerCreditRepository(session_factory=factory)

            # No bucket yet → initialize from PREMIUM allowance (500) then deduct.
            new_remaining = await port.consume(user.id, "2026-08", 5)
            assert new_remaining == 495

            # Persisted and readable back.
            assert await port.get_remaining(user.id, "2026-08") == 495

        asyncio.run(_run())


def test_consume_existing_bucket_deducts() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_bucket(factory, user.id, "2026-08", 50, 30)
            port = WorkerCreditRepository(session_factory=factory)
            new_remaining = await port.consume(user.id, "2026-08", 6)
            assert new_remaining == 24
            assert await port.get_remaining(user.id, "2026-08") == 24

        asyncio.run(_run())


def test_consume_clamps_at_zero_never_negative() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_bucket(factory, user.id, "2026-08", 50, 2)
            port = WorkerCreditRepository(session_factory=factory)
            # Requesting more than available clamps the balance at the floor.
            new_remaining = await port.consume(user.id, "2026-08", 10)
            assert new_remaining == 0
            # Persisted value is 0, never negative.
            assert await port.get_remaining(user.id, "2026-08") == 0

        asyncio.run(_run())


def test_consume_recovers_from_unique_constraint_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent insert racing on (owner_id, period_key) is retried once."""
    real_repo = SQLCreditRepository
    state = {"fail_once": True}

    class FlakyCreditRepository:
        """Delegates to the real repo but raises IntegrityError on first save."""

        def __init__(self, session):
            self._inner = real_repo(session)

        async def get_credit(self, *args, **kwargs):
            return await self._inner.get_credit(*args, **kwargs)

        async def save_credit(self, credit):
            if state["fail_once"]:
                state["fail_once"] = False
                raise IntegrityError("INSERT INTO monthly_credits", {}, Exception("dup"))
            await self._inner.save_credit(credit)

    monkeypatch.setattr(dependencies, "SQLCreditRepository", FlakyCreditRepository)

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _set_tier(factory, user.id, SubscriptionTier.FREE)
            port = WorkerCreditRepository(session_factory=factory)

            # First save raises (simulating a race), the retry succeeds.
            new_remaining = await port.consume(user.id, "2026-08", 3)
            assert new_remaining == 47  # FREE allowance 50 - 3
            assert await port.get_remaining(user.id, "2026-08") == 47

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Wallet spend order: carryover always first, and the purchased-first setting
# is honoured ONLY for API-origin jobs.
#
# Seed: carryover 5, plan 10, purchased 20, consume 8.
#   - non-preferring order spends carryover(5) then plan(3) -> (plan 7, car 0, pur 20)
#   - API + setting ON spends carryover(5) then purchased(3) -> (plan 10, car 0, pur 17)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("origin", "purchased_first", "expected"),
    [
        (JobOrigin.WEB, False, (7, 0, 20)),
        # A browser session must never burn paid credits ahead of plan credits,
        # so the setting is ignored for WEB even when it is on.
        (JobOrigin.WEB, True, (7, 0, 20)),
        (JobOrigin.API, False, (7, 0, 20)),
        (JobOrigin.API, True, (10, 0, 17)),
        (JobOrigin.GUEST, True, (7, 0, 20)),
    ],
)
def test_consume_spend_order_by_origin_and_setting(
    origin: JobOrigin, purchased_first: bool, expected: tuple[int, int, int]
) -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_wallet(
                factory,
                user.id,
                carryover=5,
                purchased=20,
                purchased_first=purchased_first,
            )
            await _seed_bucket(factory, user.id, "2026-08", 10, 10)
            port = WorkerCreditRepository(session_factory=factory)

            await port.consume(user.id, "2026-08", 8, origin=origin)

            assert await _wallet_state(factory, user.id, "2026-08") == expected

        asyncio.run(_run())


def test_consume_does_not_spend_an_expired_carryover() -> None:
    """An expired carryover is left stored (auditable) but never spent."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_wallet(
                factory,
                user.id,
                carryover=5,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                purchased=20,
            )
            await _seed_bucket(factory, user.id, "2026-08", 10, 10)
            port = WorkerCreditRepository(session_factory=factory)

            await port.consume(user.id, "2026-08", 8, origin=JobOrigin.WEB)

            # Carryover skipped; plan spent 8 of 10 and the stored 5 remains.
            assert await _wallet_state(factory, user.id, "2026-08") == (2, 5, 20)

        asyncio.run(_run())


def test_consume_spends_carryover_before_purchased_even_when_preferring_purchased() -> None:
    """Preference only orders plan vs purchased; carryover still goes first."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_wallet(factory, user.id, carryover=5, purchased=20, purchased_first=True)
            await _seed_bucket(factory, user.id, "2026-08", 10, 10)
            port = WorkerCreditRepository(session_factory=factory)

            await port.consume(user.id, "2026-08", 5, origin=JobOrigin.API)

            # Only carryover was needed.
            assert await _wallet_state(factory, user.id, "2026-08") == (10, 0, 20)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_available_total: the wallet-aware pre-check value.
# ---------------------------------------------------------------------------

def test_available_total_includes_purchased_when_plan_is_empty() -> None:
    """The bug: an API user with 0 plan credits but paid credits must not be
    refused before converting."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_wallet(factory, user.id, purchased=5)
            await _seed_bucket(factory, user.id, "2026-08", 50, 0)
            port = WorkerCreditRepository(session_factory=factory)

            assert await port.get_available_total(user.id, "2026-08") == 5

        asyncio.run(_run())


def test_available_total_falls_back_to_the_tier_grant_without_a_bucket() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            port = WorkerCreditRepository(session_factory=factory)
            # No row and no bucket -> FREE grant of 50.
            assert await port.get_available_total(user.id, "2026-08") == 50

        asyncio.run(_run())


def test_available_total_ignores_an_expired_carryover() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _create_user(factory)
            await _seed_wallet(
                factory,
                user.id,
                carryover=7,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                purchased=0,
            )
            await _seed_bucket(factory, user.id, "2026-08", 50, 0)
            port = WorkerCreditRepository(session_factory=factory)

            assert await port.get_available_total(user.id, "2026-08") == 0

        asyncio.run(_run())
