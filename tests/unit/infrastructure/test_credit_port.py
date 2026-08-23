"""Unit tests for the worker's CreditPort implementation (WorkerCreditRepository)."""

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import workers.converter_workers.dependencies as dependencies
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
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
