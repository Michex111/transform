"""The SQL upload-intent repository: the single-use transition is atomic.

The service's unit tests use an in-memory fake, which cannot prove the database
predicate. This one runs the real adapter over SQLite: two concurrent
redemptions of the same intent must yield exactly one winner, and a replay must
return ``None`` rather than a second upload.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.security.enitities.mcp_upload_intent import (
    MCPUploadIntent,
    MCPUploadIntentStatus,
)
from src.infrastructure.adapters.repository.sql_mcp_upload_intent_repo import (
    SQLMCPUploadIntentRepository,
)
from src.infrastructure.database.session import Base


def _intent(**overrides) -> MCPUploadIntent:
    now = datetime.now(UTC)
    values = {
        "id": "intent-1",
        "user_id": 7,
        "grant_id": "grant-1",
        "client_id": "client-1",
        "file_name": "report.pdf",
        "extension": "pdf",
        "max_bytes": 1024,
        "status": MCPUploadIntentStatus.PENDING,
        "created_at": now,
        "expires_at": now + timedelta(minutes=15),
    }
    values.update(overrides)
    return MCPUploadIntent(**values)


async def _setup(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'intents.db'}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def test_the_second_redemption_is_refused(tmp_path) -> None:
    async def run() -> None:
        engine, factory = await _setup(tmp_path)
        try:
            async with factory() as session:
                await SQLMCPUploadIntentRepository(session).create(_intent())

            now = datetime.now(UTC)
            async with factory() as session:
                first = await SQLMCPUploadIntentRepository(session).start_receiving(
                    "intent-1", now=now
                )
            assert first is not None
            assert first.status is MCPUploadIntentStatus.RECEIVING
            assert first.used_at is not None

            async with factory() as session:
                second = await SQLMCPUploadIntentRepository(session).start_receiving(
                    "intent-1", now=now
                )
            assert second is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_two_concurrent_redemptions_have_exactly_one_winner(tmp_path) -> None:
    async def run() -> None:
        engine, factory = await _setup(tmp_path)
        try:
            async with factory() as session:
                await SQLMCPUploadIntentRepository(session).create(_intent())

            now = datetime.now(UTC)

            async def redeem():
                async with factory() as session:
                    return await SQLMCPUploadIntentRepository(session).start_receiving(
                        "intent-1", now=now
                    )

            results = await asyncio.gather(redeem(), redeem(), return_exceptions=True)
            winners = [item for item in results if isinstance(item, MCPUploadIntent)]
            assert len(winners) == 1, results
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_an_expired_intent_cannot_be_redeemed(tmp_path) -> None:
    async def run() -> None:
        engine, factory = await _setup(tmp_path)
        try:
            past = datetime.now(UTC) - timedelta(minutes=1)
            async with factory() as session:
                await SQLMCPUploadIntentRepository(session).create(
                    _intent(expires_at=past)
                )

            async with factory() as session:
                redeemed = await SQLMCPUploadIntentRepository(session).start_receiving(
                    "intent-1", now=datetime.now(UTC)
                )
            assert redeemed is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_housekeeping_keeps_completed_rows(tmp_path) -> None:
    async def run() -> None:
        engine, factory = await _setup(tmp_path)
        try:
            past = datetime.now(UTC) - timedelta(hours=1)
            async with factory() as session:
                repository = SQLMCPUploadIntentRepository(session)
                await repository.create(_intent(id="pending", expires_at=past))
                await repository.create(
                    _intent(
                        id="completed",
                        status=MCPUploadIntentStatus.COMPLETED,
                        file_id="file-1",
                        expires_at=past,
                    )
                )
                deleted = await repository.delete_expired(datetime.now(UTC))
                assert deleted == 1
                assert await repository.get("pending") is None
                completed = await repository.get("completed")
                assert completed is not None
                assert completed.status is MCPUploadIntentStatus.COMPLETED
        finally:
            await engine.dispose()

    asyncio.run(run())
