"""Unit tests for the Stripe webhook credit-grant logic."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1 import webhooks
from src.presentation.schemas.credit import TransactionType

# A FREE-tier user starts with a 50-credit monthly allowance; purchased credits
# are added on top of that.
BASE_FREE_ALLOWANCE = 50
PURCHASED = 100
EXPECTED_BALANCE = BASE_FREE_ALLOWANCE + PURCHASED


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


def test_grant_purchased_credits_adds_allowance_and_remaining() -> None:
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
                repo = SQLCreditRepository(session)
                credit = await repo.get_credit("42", _period_key())
                assert credit is not None
                assert credit.allowance == EXPECTED_BALANCE
                assert credit.remaining == EXPECTED_BALANCE

                txns = await repo.list_transactions(42, offset=0, limit=10)
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
                repo = SQLCreditRepository(session)
                credit = await repo.get_credit("42", _period_key())
                assert credit is not None
                # Credited once, not twice.
                assert credit.allowance == EXPECTED_BALANCE
                assert credit.remaining == EXPECTED_BALANCE

                txns = await repo.list_transactions(42, offset=0, limit=10)
                assert len(txns) == 1

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
        await webhooks._handle_checkout_completed(None, event)

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

                from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
                repo = SQLCreditRepository(session)
                credit = await repo.get_credit("42", _period_key())
                assert credit is not None
                assert credit.remaining == EXPECTED_BALANCE

        asyncio.run(_run())
