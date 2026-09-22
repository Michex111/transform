"""Integration tests for the SQLAlchemy repositories, backed by SQLite."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.enitities.api_key import APIKey, APIKeyStatus
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base


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
            username="repo-user", email="repo@example.com", hashed_password="x", is_active=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def test_conversion_job_repo_roundtrip() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversionJobRepository(session)
                job = ConversionJob(
                    job_id="job-1",
                    conversion=ConversionType("pdf", "docx"),
                    input_file="input.pdf",
                    object_key="uploads/input.pdf",
                    user_id=user.id,
                )
                await repo.save_conversion_job(job)

                fetched = await repo.get_conversion_job("job-1")
                assert fetched is not None
                assert fetched.status == JobStatus.AWAITING_UPLOAD
                assert fetched.user_id == user.id
                # The creation instant survives the round-trip. It is the value
                # the API returns as `created_at`, which is how a history row
                # reports when its conversion happened.
                assert fetched.created_at is not None

                # …as do the measured byte sizes, which the detail panel shows.
                job.set_compute_result(
                    duration_ms=800, credits=2, input_size_bytes=3000, output_size_bytes=1200
                )
                await repo.update_conversion_job(job)
                sized = await repo.get_conversion_job("job-1")
                assert sized is not None
                assert sized.input_size_bytes == 3000
                assert sized.output_size_bytes == 1200

                # status transition persists
                job.pending_processing()
                await repo.update_conversion_job(job)
                updated = await repo.get_conversion_job("job-1")
                assert updated is not None
                assert updated.status == JobStatus.PENDING

                # user-scoped listing
                history, total = await repo.list_user_history(user.id, offset=0, limit=10)
                assert total == 1
                assert history[0].job_id == "job-1"
                assert history[0].created_at is not None

                counts = await repo.count_by_status(user.id)
                assert counts["TOTAL"] == 1

        asyncio.run(_run())


def test_conversion_job_repo_client_encryption_roundtrip() -> None:
    """Client-encryption metadata survives the repo save/load round-trip."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversionJobRepository(session)
                job = ConversionJob(
                    job_id="job-enc",
                    conversion=ConversionType("pdf", "docx"),
                    input_file="input.pdf",
                    object_key="uploads/input.pdf",
                    user_id=user.id,
                    client_encrypted=True,
                    data_key_wrapped="deadbeefcafe",
                )
                await repo.save_conversion_job(job)

                fetched = await repo.get_conversion_job("job-enc")
                assert fetched is not None
                assert fetched.client_encrypted is True
                assert fetched.data_key_wrapped == "deadbeefcafe"

                # update_conversion_job persists them too
                fetched.client_encrypted = False
                fetched.data_key_wrapped = None
                await repo.update_conversion_job(fetched)
                updated = await repo.get_conversion_job("job-enc")
                assert updated is not None
                assert updated.client_encrypted is False
                assert updated.data_key_wrapped is None

        asyncio.run(_run())


def test_conversion_job_active_listing() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversionJobRepository(session)
                for job_id, status in [("j1", JobStatus.PENDING), ("j2", JobStatus.COMPLETED)]:
                    await repo.save_conversion_job(
                        ConversionJob(
                            job_id=job_id,
                            conversion=ConversionType("a", "b"),
                            input_file="x",
                            status=status,
                            user_id=user.id,
                        )
                    )
                active, total = await repo.list_user_active_jobs(user.id, 0, 10)
                assert total == 1
                assert active[0].job_id == "j1"

        asyncio.run(_run())


def test_api_key_repo_roundtrip() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLAPIKeyRepository(session)
                key = APIKey(
                    id="key-1",
                    key="sha256:hash",
                    user_id=str(user.id),
                    name="test",
                    status=APIKeyStatus.ACTIVE,
                    created_at=datetime.now(UTC),
                )
                await repo.save(key)

                by_hash = await repo.find_by_key("sha256:hash")
                assert by_hash is not None
                assert by_hash.id == "key-1"

                by_user = await repo.find_by_user(user.id)
                assert len(by_user) == 1

                key.status = APIKeyStatus.REVOKED
                await repo.update(key)
                updated_key = await repo.get_by_id("key-1")
                assert updated_key is not None
                assert updated_key.status == APIKeyStatus.REVOKED

                assert await repo.delete("key-1") is True
                assert await repo.delete("key-1") is False

        asyncio.run(_run())


def test_subscription_repo_tier_and_storage() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLSubscriptionRepository(session)

                # defaults for unknown actor
                assert await repo.get_actor_tier("user:999") == SubscriptionTier.FREE

                await repo.upsert_subscription(
                    actor_key="user:1", user_id=user.id, tier=SubscriptionTier.PREMIUM
                )
                assert await repo.get_tier_for_user(user.id) == SubscriptionTier.PREMIUM

                await repo.set_used_storage_bytes("user:1", 2048)
                assert await repo.get_used_storage_bytes("user:1") == 2048

                row = await repo.get_subscription_row(user.id)
                assert row is not None
                assert row.tier == SubscriptionTier.PREMIUM

        asyncio.run(_run())


def test_credit_repo_roundtrip_and_ledger() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLCreditRepository(session)
                credit = Credit.from_tier(
                    owner_id=str(user.id), period_key="2026-08", tier=SubscriptionTier.FREE
                )
                assert credit.allowance == 50

                await repo.save_credit(credit)
                credit.remaining -= 1
                await repo.save_credit(credit)

                fetched = await repo.get_credit(str(user.id), "2026-08")
                assert fetched is not None
                assert fetched.remaining == 49

                await repo.record_transaction(
                    transaction_id="tx-1",
                    user_id=user.id,
                    amount=100,
                    transaction_type="PURCHASE",
                    reference_id="pi_123",
                )
                rows = await repo.list_transactions(user.id)
                assert len(rows) == 1
                assert rows[0].amount == 100

        asyncio.run(_run())


def test_worker_persists_status_via_repo() -> None:
    """End-to-end: the processor's status transitions persist to the DB."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                job_repo = SQLConversionJobRepository(session)
                job = ConversionJob(
                    job_id="worker-job",
                    conversion=ConversionType("txt", "md"),
                    input_file="input.txt",
                    user_id=user.id,
                    status=JobStatus.PENDING,
                )
                await job_repo.save_conversion_job(job)

            async with factory() as session:
                repo = SQLConversionJobRepository(session)
                stored = await repo.get_conversion_job("worker-job")
                assert stored is not None
                stored.status = JobStatus.COMPLETED
                stored.output_file = "output.md"
                stored.compute_duration_ms = 42
                stored.credits_used = 3
                await repo.update_conversion_job(stored)

                final = await repo.get_conversion_job("worker-job")
                assert final is not None
                assert final.status == JobStatus.COMPLETED
                assert final.output_file == "output.md"
                assert final.compute_duration_ms == 42
                assert final.credits_used == 3

        asyncio.run(_run())
