"""Integration tests for the SQLAlchemy repositories, backed by SQLite."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.services.file_listing import FileSortKey, FileSortOrder
from src.domain.assistant.entities.conversation import Message, MessageRole
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.enitities.api_key import APIKey, APIKeyStatus
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import current_period_key
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.adapters.repository.sql_assistant_account_adapter import (
    SQLAssistantAccountAdapter,
)
from src.infrastructure.adapters.repository.sql_conversation_repo import (
    SQLConversationRepository,
)
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.database.models import (
    ConversionJobModel,
    UserFileModel,
    UserFolderModel,
    UserModel,
)
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


def test_conversion_job_repo_origin_roundtrip() -> None:
    """``origin`` is persisted and read back in both directions.

    The worker's spend order depends on this value surviving the database, so a
    defaulted column that silently read back as NULL would be a real bug (the
    credit decision would fall back to WEB for API usage).
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversionJobRepository(session)
                await repo.save_conversion_job(
                    ConversionJob(
                        job_id="job-api",
                        conversion=ConversionType("pdf", "docx"),
                        input_file="input.pdf",
                        object_key="uploads/input.pdf",
                        user_id=user.id,
                        origin=JobOrigin.API,
                    )
                )
                await repo.save_conversion_job(
                    ConversionJob(
                        job_id="job-web",
                        conversion=ConversionType("pdf", "docx"),
                        input_file="input.pdf",
                        object_key="uploads/input.pdf",
                        user_id=user.id,
                    )
                )

                api_job = await repo.get_conversion_job("job-api")
                web_job = await repo.get_conversion_job("job-web")

                assert api_job is not None
                assert api_job.origin is JobOrigin.API
                assert web_job is not None
                assert web_job.origin is JobOrigin.WEB

        asyncio.run(_run())


def test_conversion_job_repo_reads_an_unknown_stored_origin_as_web() -> None:
    """A value written by a newer/other producer must not raise on read."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                session.add(
                    ConversionJobModel(
                        job_id="job-odd",
                        status=JobStatus.PENDING,
                        source_format="pdf",
                        target_format="docx",
                        input_file="input.pdf",
                        origin="CLI",
                    )
                )
                await session.commit()

                repo = SQLConversionJobRepository(session)
                fetched = await repo.get_conversion_job("job-odd")

                assert fetched is not None
                assert fetched.origin is JobOrigin.WEB

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


def test_conversion_job_aggregations_for_the_account_tool() -> None:
    """Both aggregation methods honour ``since``, ``fmt`` and ``status``.

    They feed the assistant's account tool, whose numbers must match the
    dashboard's; the tests therefore pin the exact contract (status keys, the
    OR semantics of ``fmt``, and the empty result for an unknown status).
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                other = UserModel(
                    username="other",
                    email="other@example.com",
                    hashed_password="x",
                    is_active=True,
                )
                session.add(other)
                await session.commit()
                await session.refresh(other)
                now = datetime.now(UTC)

                def job(
                    job_id: str,
                    source: str,
                    target: str,
                    status: JobStatus,
                    created_at: datetime,
                    user_id: int | None,
                ) -> ConversionJobModel:
                    return ConversionJobModel(
                        job_id=job_id,
                        status=status,
                        source_format=source,
                        target_format=target,
                        input_file="input.bin",
                        object_key="objects/input.bin",
                        user_id=user_id,
                        created_at=created_at,
                        updated_at=created_at,
                    )

                session.add_all(
                    [
                        job("a", "pdf", "docx", JobStatus.COMPLETED, now - timedelta(days=2), user.id),
                        job("b", "docx", "pdf", JobStatus.COMPLETED, now - timedelta(hours=1), user.id),
                        job("c", "pdf", "txt", JobStatus.FAILED, now - timedelta(minutes=30), user.id),
                        job("d", "png", "jpg", JobStatus.PROCESSING, now - timedelta(minutes=10), user.id),
                        job("e", "pdf", "docx", JobStatus.COMPLETED, now - timedelta(hours=1), other.id),
                    ]
                )
                await session.commit()

                repo = SQLConversionJobRepository(session)
                # Another tenant's job is never counted.
                assert await repo.counts_by_status(user.id) == {
                    "COMPLETED": 2,
                    "FAILED": 1,
                    "TOTAL": 4,
                }
                # ``since`` excludes the 2-day-old job.
                assert await repo.counts_by_status(
                    user.id, since=now - timedelta(days=1)
                ) == {"COMPLETED": 1, "FAILED": 1, "TOTAL": 3}
                # ``fmt`` matches source OR target, case-insensitively.
                assert await repo.counts_by_status(user.id, fmt="PDF") == {
                    "COMPLETED": 2,
                    "FAILED": 1,
                    "TOTAL": 3,
                }
                # ``status`` narrows to a single bucket.
                assert await repo.counts_by_status(user.id, status="failed") == {
                    "COMPLETED": 0,
                    "FAILED": 1,
                    "TOTAL": 1,
                }
                # An unknown status matches nothing rather than everything.
                assert await repo.counts_by_status(user.id, status="nope") == {
                    "COMPLETED": 0,
                    "FAILED": 0,
                    "TOTAL": 0,
                }

                # Grouped by target, most frequent first (all tie here, so the
                # format name breaks the tie).
                assert await repo.count_jobs_by_target_format(user.id) == [
                    ("docx", 1),
                    ("jpg", 1),
                    ("pdf", 1),
                    ("txt", 1),
                ]
                # ``fmt`` matches source OR target, so "pdf" keeps the docx
                # target of the pdf source, the pdf target, and the txt target.
                assert await repo.count_jobs_by_target_format(user.id, fmt="pdf") == [
                    ("docx", 1),
                    ("pdf", 1),
                    ("txt", 1),
                ]

        asyncio.run(_run())


def test_assistant_account_adapter_mirrors_the_dashboard() -> None:
    """The account adapter derives the same numbers the dashboard shows.

    Credit balance falls back to the tier allowance when no bucket exists, the
    reset date is the next period start for tiers that have an allowance, and
    "active" is everything that is neither completed nor failed.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                now = datetime.now(UTC)

                def job(
                    job_id: str, source: str, target: str, status: JobStatus
                ) -> ConversionJobModel:
                    return ConversionJobModel(
                        job_id=job_id,
                        status=status,
                        source_format=source,
                        target_format=target,
                        input_file="input.bin",
                        object_key="objects/input.bin",
                        user_id=user.id,
                        created_at=now,
                        updated_at=now,
                    )

                session.add_all(
                    [
                        job("j1", "pdf", "docx", JobStatus.COMPLETED),
                        job("j2", "docx", "pdf", JobStatus.COMPLETED),
                        job("j3", "pdf", "txt", JobStatus.FAILED),
                        job("j4", "png", "jpg", JobStatus.PENDING),
                        job("j5", "pdf", "docx", JobStatus.COMPLETED),
                    ]
                )
                await session.commit()

                adapter = SQLAssistantAccountAdapter(
                    job_repository=SQLConversionJobRepository(session),
                    credit_repository=SQLCreditRepository(session),
                    subscription_repository=SQLSubscriptionRepository(session),
                    file_repository=SQLUserFileRepository(session),
                )
                overview = await adapter.overview(
                    user.id, since=None, fmt=None, status=None
                )
                assert overview.tier == "FREE"
                assert overview.jobs_total == 5
                assert overview.jobs_completed == 3
                assert overview.jobs_failed == 1
                assert overview.jobs_active == 1
                # Ordering is by count (docx has 2), then format name.
                assert overview.by_target_format == (
                    ("docx", 2),
                    ("jpg", 1),
                    ("pdf", 1),
                    ("txt", 1),
                )
                assert overview.storage_used_bytes == 0
                assert (
                    overview.storage_limit_bytes
                    == TierPolicy.for_tier(SubscriptionTier.FREE).storage_quota_bytes
                )
                # No persisted bucket yet: the tier allowance is the balance.
                assert overview.credits_remaining == 50
                assert overview.credits_reset_at is not None

                # A persisted bucket for the current period wins over the allowance.
                await SQLCreditRepository(session).save_credit(
                    Credit(
                        owner_id=str(user.id),
                        period_key=current_period_key(now),
                        allowance=50,
                        remaining=7,
                    )
                )
                refreshed = await adapter.overview(
                    user.id, since=None, fmt=None, status=None
                )
                assert refreshed.credits_remaining == 7

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


def test_conversation_message_meta_survives_the_round_trip() -> None:
    """The UI-only meta (labels, summaries, artifacts) is persisted verbatim.

    ``meta`` is stored as a JSON string, so this proves the new keys the SPA
    reads when a conversation is reopened make it back out of the database
    unchanged — decoding must not drop or reshape them.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversationRepository(session)
                conversation = await repo.create_conversation(
                    user_id=user.id, title="Round trip"
                )

                tool_meta = {
                    "tool_call_id": "call_1",
                    "label": "Looking through your files",
                    "summary": "Found 2 matching file(s)",
                    "artifacts": [
                        {"type": "file", "id": "f1", "name": "a.pdf", "meta": {}},
                        {
                            "type": "job",
                            "id": "job-1",
                            "name": "b.docx",
                            "meta": {"status": "completed"},
                        },
                    ],
                }
                await repo.add_message(
                    Message(
                        id="m-tool",
                        conversation_id=conversation.id,
                        position=0,
                        role=MessageRole.TOOL,
                        content='{"count": 2}',
                        tool_name="list_files",
                        meta=tool_meta,
                    )
                )

                stored = await repo.list_messages(conversation.id)
                assert len(stored) == 1
                assert stored[0].meta == tool_meta

        asyncio.run(_run())


def test_truncate_from_message_deletes_the_target_and_everything_after() -> None:
    """A suffix delete keeps the ``_next_position`` COUNT invariant intact.

    The rows that survive are exactly ``0..n-1`` with no gaps, so the next
    append lands last instead of colliding with a surviving position.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                repo = SQLConversationRepository(session)
                conversation = await repo.create_conversation(
                    user_id=user.id, title="Trim"
                )
                other = await repo.create_conversation(user_id=user.id, title="Other")

                for index in range(5):
                    await repo.add_message(
                        Message(
                            id=f"m{index}",
                            conversation_id=conversation.id,
                            position=0,
                            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                            content=f"message {index}",
                        )
                    )
                await repo.add_message(
                    Message(
                        id="other-1",
                        conversation_id=other.id,
                        position=0,
                        role=MessageRole.USER,
                        content="other",
                    )
                )

                # An unknown id and a real id from another conversation both say
                # "absent", in either direction.
                assert await repo.truncate_from_message(conversation.id, "nope") is False
                assert await repo.truncate_from_message(conversation.id, "other-1") is False
                assert await repo.truncate_from_message(other.id, "m0") is False

                assert await repo.truncate_from_message(conversation.id, "m2") is True
                remaining = await repo.list_messages(conversation.id)
                assert [message.id for message in remaining] == ["m0", "m1"]
                # A different conversation is untouched.
                assert [message.id for message in await repo.list_messages(other.id)] == [
                    "other-1"
                ]

                # The next append is positioned after the survivors.
                appended = await repo.add_message(
                    Message(
                        id="m-new",
                        conversation_id=conversation.id,
                        position=0,
                        role=MessageRole.USER,
                        content="new",
                    )
                )
                assert appended.position == 2
                assert [message.id for message in await repo.list_messages(conversation.id)] == [
                    "m0",
                    "m1",
                    "m-new",
                ]

        asyncio.run(_run())


def test_user_file_search_by_name_spans_folders_and_escapes_wildcards() -> None:
    """The assistant's "find my file" search must see filed documents.

    ``list_by_user`` is root-scoped, so a search that went through it would miss
    everything inside a folder. This pins the cross-folder, case-insensitive,
    per-user, newest-first behaviour — and that ``%``/``_`` in the query are
    literals, not wildcards.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                other = UserModel(
                    username="searcher-other",
                    email="search-other@example.com",
                    hashed_password="x",
                    is_active=True,
                )
                session.add(other)
                await session.commit()
                await session.refresh(other)
                now = datetime.now(UTC)

                def row(
                    file_id: str, owner: int, folder_id: str | None, name: str,
                    created_at: datetime,
                ) -> UserFileModel:
                    return UserFileModel(
                        id=file_id,
                        user_id=owner,
                        folder_id=folder_id,
                        file_key=f"objects/{file_id}",
                        file_name=name,
                        file_extension=name.rsplit(".", 1)[-1].lower(),
                        file_size_bytes=10,
                        mime_type="application/octet-stream",
                        is_favorite=False,
                        created_at=created_at,
                    )

                session.add_all(
                    [
                        UserFolderModel(
                            id="f-outer", user_id=user.id, name="Outer",
                            parent_id=None, created_at=now, updated_at=now,
                        ),
                        UserFolderModel(
                            id="f-inner", user_id=user.id, name="Inner",
                            parent_id="f-outer", created_at=now, updated_at=now,
                        ),
                        row("a", user.id, "f-inner", "Invoice-2026.PDF", now - timedelta(days=2)),
                        row("b", user.id, None, "invoice-draft.pdf", now - timedelta(hours=1)),
                        row("c", user.id, "f-outer", "50%_report.pdf", now - timedelta(days=1)),
                        row("d", user.id, "f-inner", "axb.pdf", now - timedelta(days=3)),
                        row("e", other.id, None, "invoice-other.pdf", now - timedelta(minutes=1)),
                    ]
                )
                await session.commit()
                repo = SQLUserFileRepository(session)

                # Nested (a) and root (b) both match, case-insensitively and
                # per-user (e is another tenant's); newest first.
                rows, total = await repo.search_by_name(user.id, "INVOICE")
                assert total == 2
                assert [r.id for r in rows] == ["b", "a"]

                # The total is the full match count, independent of the page.
                page, total_all = await repo.search_by_name(user.id, "invoice", limit=1)
                assert total_all == 2
                assert [r.id for r in page] == ["b"]

                # ``%`` and ``_`` match literally, not as wildcards.
                escaped, escaped_total = await repo.search_by_name(user.id, "50%_report")
                assert escaped_total == 1
                assert [r.id for r in escaped] == ["c"]
                # ``a_b`` would match ``axb.pdf`` if ``_`` were a wildcard.
                _, underscore_total = await repo.search_by_name(user.id, "a_b")
                assert underscore_total == 0
                # ``%`` would match every row if it were a wildcard; escaped, it
                # matches only the name that literally contains one.
                percent_rows, percent_total = await repo.search_by_name(user.id, "%")
                assert percent_total == 1
                assert [r.id for r in percent_rows] == ["c"]

        asyncio.run(_run())


def test_file_listings_can_be_ranked_by_size_across_folders() -> None:
    """The reported bug, at the layer that has to get it right.

    "What is my largest file?" is a question about the whole drive, so it cannot
    go through ``list_by_user`` (whose omitted ``folder_id`` means the root) and
    it cannot be sorted in the caller (which would only rank a page). Both halves
    are pinned here: the drive-wide scope, and that the database applies the
    ordering *before* ``offset``/``limit`` so ``limit=1`` really is one row.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                now = datetime.now(UTC)

                def row(
                    file_id: str, folder_id: str | None, name: str, size: int,
                    created_at: datetime,
                ) -> UserFileModel:
                    return UserFileModel(
                        id=file_id,
                        user_id=user.id,
                        folder_id=folder_id,
                        file_key=f"objects/{file_id}",
                        file_name=name,
                        file_extension=name.rsplit(".", 1)[-1].lower(),
                        file_size_bytes=size,
                        mime_type="application/octet-stream",
                        is_favorite=False,
                        created_at=created_at,
                    )

                session.add_all(
                    [
                        UserFolderModel(
                            id="f-decks", user_id=user.id, name="Decks",
                            parent_id=None, created_at=now, updated_at=now,
                        ),
                        # The biggest file is filed away, NOT at the root — the
                        # whole reason a root-only listing cannot answer this.
                        row("small", None, "notes.txt", 2_000, now - timedelta(days=4)),
                        row("biggest", "f-decks", "conference.key", 90_000_000, now - timedelta(days=3)),
                        row("middle", None, "photo.png", 400_000, now - timedelta(days=2)),
                    ]
                )
                await session.commit()
                repo = SQLUserFileRepository(session)

                rows, total = await repo.list_all_by_user(
                    user.id, sort=FileSortKey.SIZE, order=FileSortOrder.DESC, limit=1
                )
                assert total == 3
                assert [r.id for r in rows] == ["biggest"]

                # Ascending is the other end of the same ranking.
                rows, _ = await repo.list_all_by_user(
                    user.id, sort=FileSortKey.SIZE, order=FileSortOrder.ASC, limit=2
                )
                assert [r.id for r in rows] == ["small", "middle"]

                # The default is unchanged: newest first, root-scoped only.
                root, root_total = await repo.list_by_user(user.id)
                assert root_total == 2
                assert [r.id for r in root] == ["middle", "small"]

                # A folder listing can be ranked too.
                decks, _ = await repo.list_by_user(
                    user.id, folder_id="f-decks", sort=FileSortKey.NAME
                )
                assert [r.id for r in decks] == ["biggest"]

        asyncio.run(_run())


def test_file_listing_ranking_is_deterministic_under_ties() -> None:
    """Equal sort keys must still produce one fixed order.

    Two files of the same size are the normal case, not an edge case, and a
    backend is free to return them in any order. Without the ``id`` tie-break,
    "the largest file" could name a different file on the next call, which reads
    to the user as the assistant changing its mind.
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                now = datetime.now(UTC)
                for file_id in ("z-last", "a-first", "m-middle"):
                    session.add(
                        UserFileModel(
                            id=file_id,
                            user_id=user.id,
                            folder_id=None,
                            file_key=f"objects/{file_id}",
                            file_name=f"{file_id}.bin",
                            file_extension="bin",
                            file_size_bytes=5_000,
                            mime_type="application/octet-stream",
                            is_favorite=False,
                            created_at=now,
                        )
                    )
                await session.commit()
                repo = SQLUserFileRepository(session)

                first, _ = await repo.list_all_by_user(user.id, sort=FileSortKey.SIZE)
                again, _ = await repo.list_all_by_user(user.id, sort=FileSortKey.SIZE)
                ids = [r.id for r in first]
                assert ids == [r.id for r in again]
                assert ids == sorted(ids)

        asyncio.run(_run())


def test_conversion_job_search_filters_by_name_and_format_per_user() -> None:
    """``search_jobs`` matches either end of a conversion, newest first."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _create_user(factory)
                other = UserModel(
                    username="job-other",
                    email="job-other@example.com",
                    hashed_password="x",
                    is_active=True,
                )
                session.add(other)
                await session.commit()
                await session.refresh(other)
                now = datetime.now(UTC)

                def job(
                    job_id: str, source: str, target: str, input_file: str,
                    output_file: str | None, created_at: datetime, owner: int,
                ) -> ConversionJobModel:
                    return ConversionJobModel(
                        job_id=job_id,
                        status=JobStatus.COMPLETED,
                        source_format=source,
                        target_format=target,
                        input_file=input_file,
                        output_file=output_file,
                        object_key=f"objects/{job_id}",
                        user_id=owner,
                        created_at=created_at,
                        updated_at=created_at,
                    )

                session.add_all(
                    [
                        job("j1", "pdf", "docx", "homework.pdf", "homework.docx", now - timedelta(days=2), user.id),
                        job("j2", "docx", "pdf", "essay.docx", "essay.pdf", now - timedelta(hours=1), user.id),
                        job("j3", "png", "jpg", "photo.png", "photo.jpg", now - timedelta(minutes=30), user.id),
                        job("j4", "pdf", "docx", "homework-other.pdf", None, now, other.id),
                    ]
                )
                await session.commit()
                repo = SQLConversionJobRepository(session)

                # Name substring, case-insensitive, on either side, per-user.
                assert [j.job_id for j in await repo.search_jobs(user.id, query="HOMEWORK")] == ["j1"]
                assert [j.job_id for j in await repo.search_jobs(user.id, query="essay.pdf")] == ["j2"]
                # Format matches source OR target, case-insensitively.
                assert [j.job_id for j in await repo.search_jobs(user.id, fmt="PDF")] == ["j2", "j1"]
                # Filters combine with AND; the limit keeps the newest.
                assert [j.job_id for j in await repo.search_jobs(user.id, query="homework", fmt="docx")] == ["j1"]
                assert [j.job_id for j in await repo.search_jobs(user.id, fmt="pdf", limit=1)] == ["j2"]
                # No filters is an unfiltered listing, still per-user.
                assert [j.job_id for j in await repo.search_jobs(user.id)] == ["j3", "j2", "j1"]
                # ``%`` in a query is literal, so it matches nothing here.
                assert await repo.search_jobs(user.id, query="%") == []

        asyncio.run(_run())


def test_get_user_storage_used_always_returns_int() -> None:
    """Regression: PostgreSQL hands back ``Decimal`` for ``SUM(BIGINT)``.

    The repository's contract is ``int`` (every caller — quota arithmetic, the
    dashboard, the assistant account snapshot — treats it as one, and the
    declared return type says so). asyncpg maps the ``numeric`` type PostgreSQL
    uses for ``sum(bigint)`` to ``Decimal``, while SQLite returns a plain int,
    so a test backed by the real SQLite engine cannot see the regression. Feeding
    the repository exactly what Postgres produces is what makes this fail on the
    coercing code being removed.
    """
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    session = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = Decimal("1048576")
    session.execute = AsyncMock(return_value=result)

    repo = SQLUserFileRepository(session=session)
    value = asyncio.run(repo.get_user_storage_used(1))

    assert type(value) is int, f"expected int, got {type(value).__name__}"
    assert value == 1048576
