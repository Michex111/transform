"""End-to-end tests for the assistant API over an in-memory SQLite database.

The real app is used with only its edges swapped (auth, object storage, the
model transport, the conversion queue, the quota counter), so these tests cover
what a unit test cannot: the actual dependency graph, the SQLite persistence of
the transcript, and the SSE bytes a browser would receive.

The recipe (lazy ``SqliteBackend`` + a ``TestClient`` with overrides) is the one
already used by ``test_folder_endpoints`` and ``test_upload_verify_flow``; the
addition here is overriding ``get_db_session`` itself, which lets the real
SQLAlchemy repositories (and the 401-on-missing-token path) run against SQLite
instead of the configured production database.
"""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.dtos.upload_dto import UploadResponse
from src.application.services.file_service import FileService
from src.domain.assistant.exceptions.assistant_exceptions import AssistantQuotaExceeded
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_conversation_repo import (
    SQLConversationRepository,
)
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFileModel, UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import (
    get_assistant_model_registry,
    get_assistant_quota,
    get_conversion_service,
    get_db_session,
    get_file_service,
    get_minio_url_storage,
    get_subscription_repository,
)
from tests.fakes.fake_assistant_model_resolver import FakeAssistantModelResolver
from tests.fakes.fake_llm_port import FakeLlmPort, text_response, tool_response
from tests.fixtures.documents import docx_bytes, minimal_pdf, zip_bytes

USER_ID = 1
OTHER_USER_ID = 2


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeUser:
    id: int


class FakeSubscriptionRepo:
    def __init__(self, tier: SubscriptionTier = SubscriptionTier.FREE) -> None:
        self.tier = tier

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        del user_id
        return self.tier


class FakeStorage:
    """Keyed in-memory object store + the metadata methods the services use."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def stat_object(self, object_key: str) -> dict | None:
        payload = self.objects.get(object_key)
        if payload is None:
            return None
        return {"size": len(payload), "content_type": "application/octet-stream"}

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        return self.objects.get(object_key, b"")[:max_bytes]

    async def remove_object(self, object_key: str) -> bool:
        return self.objects.pop(object_key, None) is not None

    def generate_put_url(self, object_key: str) -> str:
        return f"https://storage.test/put/{object_key}"

    def generate_get_url(self, object_key: str, expires_in_minutes: int = 60) -> str:
        del expires_in_minutes
        return f"https://storage.test/get/{object_key}"

    async def object_exists(self, object_key: str) -> bool:
        return object_key in self.objects


class FakeConversionService:
    def __init__(self) -> None:
        self.converted: list[dict[str, Any]] = []
        self.jobs: dict[str, ConversionJob] = {}

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> ConversionJob:
        self.converted.append(
            {
                "file_name": file_name,
                "source_format": source_format,
                "target_format": target_format,
                "object_key": object_key,
                "user_id": user_id,
                "tier": tier,
            }
        )
        job = ConversionJob(
            job_id=f"job-{len(self.converted)}",
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=file_name,
            object_key=object_key,
            user_id=user_id,
            status=JobStatus.PENDING,
        )
        self.jobs[job.job_id] = job
        return job

    async def create_upload(self, *args: Any, **kwargs: Any) -> UploadResponse:
        raise AssertionError("not used in these tests")

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.jobs.get(job_id)

    async def list_history(
        self, user_id: int, *, offset: int = 0, limit: int = 20, since: Any = None
    ) -> tuple[list[ConversionJob], int]:
        del since
        rows = [job for job in self.jobs.values() if job.user_id == user_id]
        return rows[offset : offset + limit], len(rows)


class FakeQuota:
    def __init__(self, error: Exception | None = None, used: int = 0) -> None:
        self.error = error
        self.used = used
        self.consumed: list[tuple[int, int]] = []

    async def consume(self, user_id: int, limit: int) -> None:
        if self.error is not None:
            raise self.error
        self.consumed.append((user_id, limit))

    async def peek(self, user_id: int) -> int:
        del user_id
        return self.used


class SqliteBackend:
    """Lazily creates the SQLite engine inside the app's event loop.

    NullPool plus a file-backed database avoids the loop-binding and
    connection-leak problems of ``:memory:`` SQLite under ``TestClient``.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._factory: async_sessionmaker | None = None

    async def ensure(self) -> async_sessionmaker:
        if self._factory is None:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}", poolclass=NullPool
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                # Idempotent: ``ensure`` may run against a database another
                # backend instance (e.g. the ownership helper) already seeded.
                existing_users = await session.scalar(
                    select(func.count()).select_from(UserModel)
                )
                if not existing_users:
                    now = datetime.now(UTC)
                    session.add_all(
                        [
                            UserModel(
                                id=USER_ID,
                                username="assistant-user",
                                email="assistant@example.com",
                                hashed_password="x",
                                is_active=True,
                                created_at=now,
                            ),
                            UserModel(
                                id=OTHER_USER_ID,
                                username="other-user",
                                email="other@example.com",
                                hashed_password="x",
                                is_active=True,
                                created_at=now,
                            ),
                        ]
                    )
                    await session.commit()
            self._factory = factory
        return self._factory


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Harness:
    """The knobs a test needs: the client, the fakes it was built with."""

    def __init__(
        self,
        client: TestClient,
        storage: FakeStorage,
        conversions: FakeConversionService,
        quota: FakeQuota,
        llm: FakeLlmPort,
        tier: SubscriptionTier,
        models: FakeAssistantModelResolver,
    ) -> None:
        self.client = client
        self.storage = storage
        self.conversions = conversions
        self.quota = quota
        self.llm = llm
        self.tier = tier
        self.models = models


def _text_file(name: str, key: str, payload: bytes, user_id: int = USER_ID) -> UserFileModel:
    return UserFileModel(
        id=key,
        user_id=user_id,
        folder_id=None,
        file_key=key,
        file_name=name,
        file_extension=name.rsplit(".", 1)[-1].lower(),
        file_size_bytes=len(payload),
        mime_type="application/octet-stream",
        is_favorite=False,
        created_at=datetime.now(UTC),
    )


@contextmanager
def assistant_app(
    db_path: str,
    *,
    seed_files: list[tuple[str, str, bytes, int]] | None = None,
    llm: FakeLlmPort | None = None,
    models: FakeAssistantModelResolver | None = None,
    tier: SubscriptionTier = SubscriptionTier.FREE,
    quota_error: Exception | None = None,
) -> Iterator[Harness]:
    """Build the real app with SQLite persistence and stubbed edges."""
    rows: list[UserFileModel] = []
    objects: dict[str, bytes] = {}
    for name, key, payload, owner in seed_files or []:
        rows.append(_text_file(name, key, payload, owner))
        objects[key] = payload

    storage = FakeStorage(objects)
    conversions = FakeConversionService()
    quota = FakeQuota(quota_error)
    model = llm or FakeLlmPort([text_response("I can help with that.")])
    # Every tier is served the same injected model unless a test supplies a
    # per-tier map; either way the transport is resolved through the same port
    # the production registry implements.
    resolver = models or FakeAssistantModelResolver(default=model)

    async def no_op_initialize_database() -> None:
        return None

    backend = SqliteBackend(db_path)

    async def override_db_session():
        # Seeding happens for every session creation: ``ensure`` is memoised, so
        # the first request pays for the schema and the seeded rows.
        factory = await backend.ensure()
        async with factory() as session:
            for row in rows:
                if await session.get(UserFileModel, row.id) is None:
                    session.add(row)
            await session.commit()
            yield session

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=storage,
                subscription_repository=FakeSubscriptionRepo(tier),
            )

    original_initialize_database = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=USER_ID)
    api_main.app.dependency_overrides[get_db_session] = override_db_session
    api_main.app.dependency_overrides[get_file_service] = override_file_service
    api_main.app.dependency_overrides[get_minio_url_storage] = lambda: storage
    api_main.app.dependency_overrides[get_conversion_service] = lambda: conversions
    api_main.app.dependency_overrides[get_assistant_quota] = lambda: quota
    api_main.app.dependency_overrides[get_assistant_model_registry] = lambda: resolver
    api_main.app.dependency_overrides[get_subscription_repository] = (
        lambda: FakeSubscriptionRepo(tier)
    )

    client = TestClient(api_main.app)
    try:
        yield Harness(client, storage, conversions, quota, model, tier, resolver)
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_initialize_database


def _seed_conversation(db_path: str, user_id: int, title: str = "Theirs") -> str:
    """Create a conversation row directly, for ownership tests."""

    async def _create() -> str:
        factory = await SqliteBackend(db_path).ensure()
        async with factory() as session:
            conversation = await SQLConversationRepository(session).create_conversation(
                user_id=user_id, title=title
            )
            return conversation.id

    return asyncio.run(_create())


def _frames(response) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into ``(event, payload)`` pairs."""
    frames: list[tuple[str, dict[str, Any]]] = []
    event: str | None = None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event is not None:
            frames.append((event, json.loads(line[len("data: ") :])))
            event = None
    return frames


def _chat(
    harness: Harness,
    message: str,
    conversation_id: str | None = None,
    *,
    file_ids: list[str] | None = None,
    context: str | None = None,
):
    payload: dict[str, Any] = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if file_ids is not None:
        payload["file_ids"] = file_ids
    if context is not None:
        payload["context"] = context
    return harness.client.post("/api/v1/assistant/chat", json=payload)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_requires_authentication(tmp_path) -> None:
    with assistant_app(str(tmp_path / "auth.db")) as harness:
        del api_main.app.dependency_overrides[get_current_user]
        assert harness.client.get("/api/v1/assistant/status").status_code == 401
        assert harness.client.get("/api/v1/assistant/conversations").status_code == 401
        assert harness.client.post(
            "/api/v1/assistant/chat", json={"message": "hi"}
        ).status_code == 401


def test_status_reports_the_resolved_backend_and_access(tmp_path) -> None:
    with assistant_app(str(tmp_path / "status.db")) as harness:
        body = harness.client.get("/api/v1/assistant/status").json()
        assert body["enabled"] is True
        # conftest pins AI_BACKEND=echo so this does not depend on a local .env.
        assert body["backend"] == "echo"


def test_status_is_disabled_for_a_tier_without_allowance(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "guest.db"), tier=SubscriptionTier.GUEST
    ) as harness:
        assert harness.client.get("/api/v1/assistant/status").json()["enabled"] is False


def test_status_reports_the_tier_entitlements_and_usage(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "status-ent.db"), tier=SubscriptionTier.PRO
    ) as harness:
        harness.quota.used = 3
        body = harness.client.get("/api/v1/assistant/status").json()
    assert body["tier"] == "PRO"
    assert body["model_level"] == "advanced"
    assert body["model_label"] == "Advanced"
    assert body["requests_per_hour"] == 60
    assert body["used_this_hour"] == 3
    assert body["remaining_this_hour"] == 57
    assert body["max_attachments"] == 3
    assert body["max_actions_per_turn"] == 3
    assert body["max_document_bytes"] == 25 * 1024 * 1024


def test_status_reports_the_tier_model(tmp_path) -> None:
    """``/status`` reports the model resolved for the caller's tier."""
    standard = FakeLlmPort([text_response("std")], model="standard-model")
    advanced = FakeLlmPort([text_response("adv")], model="advanced-model")
    resolver = FakeAssistantModelResolver(
        default=standard, by_tier={SubscriptionTier.PRO: advanced}
    )
    with assistant_app(
        str(tmp_path / "status-model.db"), models=resolver, tier=SubscriptionTier.PRO
    ) as harness:
        assert harness.client.get("/api/v1/assistant/status").json()["model"] == (
            "advanced-model"
        )


# ---------------------------------------------------------------------------
# Chat streaming
# ---------------------------------------------------------------------------


def test_the_model_serving_a_turn_is_the_tiers(tmp_path) -> None:
    """A PRO turn must use PRO's transport, never the default one."""
    standard = FakeLlmPort([text_response("std")], model="standard-model")
    advanced = FakeLlmPort([text_response("adv")], model="advanced-model")
    resolver = FakeAssistantModelResolver(
        default=standard, by_tier={SubscriptionTier.PRO: advanced}
    )
    with assistant_app(
        str(tmp_path / "turn-model.db"), models=resolver, tier=SubscriptionTier.PRO
    ) as harness:
        response = _chat(harness, "hello")
        assert response.status_code == 200
        _frames(response)
    assert advanced.calls
    assert standard.calls == []


def test_a_free_caller_cannot_send_two_attachments(tmp_path) -> None:
    files = [
        ("a.pdf", "objects/a.pdf", b"%PDF-1.4", USER_ID),
        ("b.pdf", "objects/b.pdf", b"%PDF-1.4", USER_ID),
    ]
    with assistant_app(str(tmp_path / "att-free.db"), seed_files=files) as harness:
        response = _chat(
            harness, "read these", file_ids=["objects/a.pdf", "objects/b.pdf"]
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ATTACHMENT_LIMIT_EXCEEDED"


def test_a_pro_caller_can_send_two_attachments(tmp_path) -> None:
    files = [
        ("a.pdf", "objects/a.pdf", b"%PDF-1.4", USER_ID),
        ("b.pdf", "objects/b.pdf", b"%PDF-1.4", USER_ID),
    ]
    with assistant_app(
        str(tmp_path / "att-pro.db"), seed_files=files, tier=SubscriptionTier.PRO
    ) as harness:
        response = _chat(
            harness, "read these", file_ids=["objects/a.pdf", "objects/b.pdf"]
        )
        assert response.status_code == 200
        _frames(response)


def test_chat_streams_a_tool_call_and_persists_the_transcript(tmp_path) -> None:
    llm = FakeLlmPort(
        [tool_response("list_files", {"query": "report"}), text_response("You have one report.")]
    )
    with assistant_app(
        str(tmp_path / "chat.db"),
        seed_files=[("report.pdf", "objects/report.pdf", b"%PDF-1.4", USER_ID)],
        llm=llm,
    ) as harness:
        response = _chat(harness, "what report.pdf files do I have?")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = _frames(response)

        names = [name for name, _ in frames]
        assert names[0] == "status"
        assert frames[0][1] == {"stage": "thinking"}
        assert "tool" in names
        assert "artifact" in names
        assert names[-1] == "done"

        tool_done = [
            payload for name, payload in frames if name == "tool" and payload["status"] == "done"
        ]
        assert tool_done and tool_done[0]["summary"]
        assert [payload["id"] for name, payload in frames if name == "artifact"] == [
            "objects/report.pdf"
        ]

        done = frames[-1][1]
        assert done["content"] == "You have one report."
        assert done["conversation_id"]
        assert done["message_id"]
        assert done["user_message_id"]
        assert harness.quota.consumed == [(USER_ID, 20)]

        # The transcript is readable through the API, with both sides of the pair.
        detail = harness.client.get(
            f"/api/v1/assistant/conversations/{done['conversation_id']}"
        ).json()
        # The done frame's user_message_id is the persisted user row, which is
        # what lets the client target a server-side truncate without a reload.
        assert detail["messages"][0]["id"] == done["user_message_id"]
        assert [message["role"] for message in detail["messages"]] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert detail["messages"][2]["tool_name"] == "list_files"

        # …and a REOPENED conversation can rebuild the tool step from stored
        # meta: the label, summary and artifacts the live SSE frames carried.
        tool_meta = detail["messages"][2]["meta"]
        assert tool_meta["tool_call_id"]
        assert tool_meta["label"] == "Looking through your files"
        assert tool_meta["summary"]
        assert [artifact["id"] for artifact in tool_meta["artifacts"]] == [
            "objects/report.pdf"
        ]
        # The tool-request message keeps only the provider's replay payload.
        assert set(detail["messages"][1]["meta"]) == {"tool_calls"}
        # The final answer carries the turn's accumulated artifacts.
        assert [artifact["id"] for artifact in detail["messages"][3]["meta"]["artifacts"]] == [
            "objects/report.pdf"
        ]

    # ... and the model was handed the tool result, not left to guess.
    assert llm.calls[1][0][-1].role == "tool"


def test_chat_surfaces_a_folder_artifact_after_create_folder(tmp_path) -> None:
    """A created folder must reach the client as a clickable artifact.

    The whole point of the change: the answer says "I made the folder", and the
    user needs something to click. The artifact is asserted both on the live
    ``done`` frame and in the persisted transcript meta, because a reopened
    conversation rebuilds the turn from storage.
    """
    llm = FakeLlmPort(
        [
            tool_response("create_folder", {"name": "Invoices"}),
            text_response("I created the Invoices folder."),
        ]
    )
    with assistant_app(str(tmp_path / "folder-chat.db"), llm=llm) as harness:
        frames = _frames(_chat(harness, "make me an Invoices folder"))
        done = frames[-1][1]
        assert [artifact["type"] for artifact in done["artifacts"]] == ["folder"]
        folder = done["artifacts"][0]
        assert folder["name"] == "Invoices"
        assert folder["id"]
        assert folder["meta"] == {"parent_id": None}

        detail = harness.client.get(
            f"/api/v1/assistant/conversations/{done['conversation_id']}"
        ).json()
        tool_message = detail["messages"][2]
        assert tool_message["tool_name"] == "create_folder"
        assert [artifact["id"] for artifact in tool_message["meta"]["artifacts"]] == [
            folder["id"]
        ]
        assert [artifact["type"] for artifact in detail["messages"][3]["meta"]["artifacts"]] == [
            "folder"
        ]


def test_chat_lists_the_conversation_afterwards(tmp_path) -> None:
    with assistant_app(str(tmp_path / "list.db")) as harness:
        done = _frames(_chat(harness, "Hello there"))[-1][1]
        listing = harness.client.get("/api/v1/assistant/conversations").json()

    assert [row["id"] for row in listing["conversations"]] == [done["conversation_id"]]
    assert listing["conversations"][0]["title"] == "Hello there"


def test_chat_with_attachments_streams_and_persists_the_meta(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "attach.db"),
        seed_files=[("report.pdf", "objects/report.pdf", b"%PDF-1.4", USER_ID)],
    ) as harness:
        response = _chat(harness, "summarise this", file_ids=["objects/report.pdf"])
        assert response.status_code == 200
        frames = _frames(response)
        assert frames[-1][0] == "done"

        conversation_id = frames[-1][1]["conversation_id"]
        detail = harness.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        ).json()
        # The attachment is not echoed in the SSE frames, but a reopened
        # conversation can rebuild the chip from the stored user-message meta.
        user_message = detail["messages"][0]
        assert user_message["role"] == "user"
        assert user_message["meta"]["attachments"] == [
            {"id": "objects/report.pdf", "name": "report.pdf", "extension": "pdf"}
        ]


def test_chat_rejects_an_unowned_attachment(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "attach-foreign.db"),
        seed_files=[("secret.pdf", "objects/secret.pdf", b"%PDF-1.4", OTHER_USER_ID)],
    ) as harness:
        response = _chat(harness, "read it", file_ids=["objects/secret.pdf"])
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTACHMENT_NOT_FOUND"


def test_chat_rejects_an_unknown_attachment(tmp_path) -> None:
    with assistant_app(str(tmp_path / "attach-missing.db")) as harness:
        response = _chat(harness, "read it", file_ids=["objects/does-not-exist"])
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTACHMENT_NOT_FOUND"


def test_chat_rejects_a_conversation_owned_by_another_user(tmp_path) -> None:
    db_path = str(tmp_path / "ownership.db")
    with assistant_app(db_path) as harness:
        foreign = _seed_conversation(db_path, OTHER_USER_ID)
        response = _chat(harness, "Hello", conversation_id=foreign)
        # Indistinguishable from "does not exist", so ids stay unenumerable.
        assert response.status_code == 404
        assert harness.client.get(
            f"/api/v1/assistant/conversations/{foreign}"
        ).status_code == 404
        assert harness.client.delete(
            f"/api/v1/assistant/conversations/{foreign}"
        ).status_code == 404


def test_chat_returns_429_when_the_quota_is_spent(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "quota.db"), quota_error=AssistantQuotaExceeded("spent")
    ) as harness:
        response = _chat(harness, "Hello")
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "QUOTA_EXCEEDED"


def test_chat_returns_403_for_a_tier_without_allowance(tmp_path) -> None:
    with assistant_app(str(tmp_path / "tier.db"), tier=SubscriptionTier.GUEST) as harness:
        response = _chat(harness, "Hello")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AI_NOT_AVAILABLE_FOR_TIER"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_conversation_lifecycle(tmp_path) -> None:
    with assistant_app(str(tmp_path / "lifecycle.db")) as harness:
        created = harness.client.post(
            "/api/v1/assistant/conversations", json={"title": "Tax questions"}
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        detail = harness.client.get(f"/api/v1/assistant/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert detail.json()["conversation"]["title"] == "Tax questions"
        assert detail.json()["messages"] == []

        deleted = harness.client.delete(f"/api/v1/assistant/conversations/{conversation_id}")
        assert deleted.status_code == 204
        assert harness.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        ).status_code == 404


def test_create_conversation_defaults_its_title(tmp_path) -> None:
    with assistant_app(str(tmp_path / "default-title.db")) as harness:
        created = harness.client.post("/api/v1/assistant/conversations", json={})
    assert created.status_code == 201
    assert created.json()["title"] == "New chat"


def test_truncate_from_message_removes_the_tail_and_the_turn_can_be_resent(tmp_path) -> None:
    with assistant_app(str(tmp_path / "truncate.db")) as harness:
        first = _frames(_chat(harness, "First question"))[-1][1]
        conversation_id = first["conversation_id"]

        second = _frames(
            _chat(harness, "Second question", conversation_id=conversation_id)
        )[-1][1]

        before = harness.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        ).json()
        assert [message["role"] for message in before["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]

        # Truncate from the second user message: it and its reply disappear,
        # while the first exchange survives.
        response = harness.client.delete(
            f"/api/v1/assistant/conversations/{conversation_id}"
            f"/messages/{second['user_message_id']}"
        )
        assert response.status_code == 204

        after = harness.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        ).json()
        assert [message["content"] for message in after["messages"]] == [
            "First question",
            "I can help with that.",
        ]

        # An "edit and resend" then appends after the survivors.
        _frames(_chat(harness, "Second question (edited)", conversation_id=conversation_id))
        resent = harness.client.get(
            f"/api/v1/assistant/conversations/{conversation_id}"
        ).json()
        assert [message["content"] for message in resent["messages"]] == [
            "First question",
            "I can help with that.",
            "Second question (edited)",
            "All done.",
        ]


def test_truncate_rejects_a_conversation_owned_by_another_user(tmp_path) -> None:
    db_path = str(tmp_path / "truncate-ownership.db")
    with assistant_app(db_path) as harness:
        foreign = _seed_conversation(db_path, OTHER_USER_ID)
        response = harness.client.delete(
            f"/api/v1/assistant/conversations/{foreign}/messages/msg-x"
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


def test_truncate_rejects_an_unknown_or_foreign_message_id(tmp_path) -> None:
    with assistant_app(str(tmp_path / "truncate-missing.db")) as harness:
        conversation_id = _frames(_chat(harness, "Hello"))[-1][1]["conversation_id"]

        unknown = harness.client.delete(
            f"/api/v1/assistant/conversations/{conversation_id}/messages/does-not-exist"
        )
        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "Message not found"

        # A real message id, but from a different conversation: indistinguishable
        # from an unknown one, so message ids stay unenumerable.
        other = _frames(_chat(harness, "Elsewhere"))[-1][1]
        foreign = harness.client.delete(
            f"/api/v1/assistant/conversations/{conversation_id}"
            f"/messages/{other['user_message_id']}"
        )
        assert foreign.status_code == 404
        assert foreign.json()["detail"] == "Message not found"


# ---------------------------------------------------------------------------
# Summarise and recommend
# ---------------------------------------------------------------------------


def test_summarize_reads_a_text_file(tmp_path) -> None:
    files = [
        ("notes.txt", "objects/notes.txt", b"First line.\nSecond line about invoices.", USER_ID),
    ]
    with assistant_app(
        str(tmp_path / "sum-txt.db"), seed_files=files, llm=FakeLlmPort(model="echo")
    ) as harness:
        response = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "objects/notes.txt"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["file_id"] == "objects/notes.txt"
    assert body["file_name"] == "notes.txt"
    assert body["summary"]
    assert isinstance(body["key_points"], list)
    assert body["model"] == "echo"


def test_summarize_reads_a_pdf_and_a_docx(tmp_path) -> None:
    files = [
        ("report.pdf", "objects/report.pdf", minimal_pdf("Quarterly revenue report"), USER_ID),
        ("memo.docx", "objects/memo.docx", docx_bytes(), USER_ID),
    ]
    with assistant_app(
        str(tmp_path / "sum-docs.db"), seed_files=files, llm=FakeLlmPort(model="echo")
    ) as harness:
        pdf = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "objects/report.pdf"}
        )
        docx = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "objects/memo.docx"}
        )
    assert pdf.status_code == 200
    assert "Quarterly" in pdf.json()["summary"]
    assert docx.status_code == 200


def test_summarize_rejects_an_unsupported_format(tmp_path) -> None:
    files = [("bundle.zip", "objects/bundle.zip", zip_bytes(), USER_ID)]
    with assistant_app(str(tmp_path / "sum-zip.db"), seed_files=files) as harness:
        response = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "objects/bundle.zip"}
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DOCUMENT_NOT_READABLE"


def test_summarize_does_not_leak_another_users_file(tmp_path) -> None:
    files = [("secret.txt", "objects/secret.txt", b"top secret", OTHER_USER_ID)]
    with assistant_app(str(tmp_path / "sum-owner.db"), seed_files=files) as harness:
        response = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "objects/secret.txt"}
        )
    assert response.status_code == 404


def test_summarize_returns_404_for_an_unknown_file(tmp_path) -> None:
    with assistant_app(str(tmp_path / "sum-missing.db")) as harness:
        response = harness.client.post(
            "/api/v1/assistant/summarize", json={"file_id": "nope"}
        )
    assert response.status_code == 404


def test_recommend_returns_a_ranked_list(tmp_path) -> None:
    with assistant_app(
        str(tmp_path / "recommend.db"), llm=FakeLlmPort(model="echo")
    ) as harness:
        response = harness.client.post(
            "/api/v1/assistant/recommend",
            json={"source_format": "docx", "use_case": "put it on the web"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["source_format"] == "docx"
    assert body["use_case"] == "put it on the web"
    assert 1 <= len(body["recommendations"]) <= 4
    first = body["recommendations"][0]
    assert set(first) == {"target_format", "label", "category", "reason", "confidence"}
    assert 0.0 <= first["confidence"] <= 1.0
    confidences = [item["confidence"] for item in body["recommendations"]]
    assert confidences == sorted(confidences, reverse=True)


def test_recommend_infers_the_format_from_a_file(tmp_path) -> None:
    files = [("sheet.xlsx", "objects/sheet.xlsx", b"PK", USER_ID)]
    with assistant_app(
        str(tmp_path / "recommend-file.db"), seed_files=files, llm=FakeLlmPort(model="echo")
    ) as harness:
        response = harness.client.post(
            "/api/v1/assistant/recommend", json={"file_id": "objects/sheet.xlsx"}
        )
    assert response.status_code == 200
    assert response.json()["source_format"] == "xlsx"


def test_recommend_requires_a_source_and_rejects_an_unknown_one(tmp_path) -> None:
    with assistant_app(str(tmp_path / "recommend-bad.db")) as harness:
        assert harness.client.post("/api/v1/assistant/recommend", json={}).status_code == 422
        unknown = harness.client.post(
            "/api/v1/assistant/recommend", json={"source_format": "nope"}
        )
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "NO_RECOMMENDATION"


# ---------------------------------------------------------------------------
# Tools acting on real data
# ---------------------------------------------------------------------------


def test_start_conversion_tool_enqueues_a_real_job(tmp_path) -> None:
    llm = FakeLlmPort(
        [
            tool_response("list_files", {"query": "report.pdf"}),
            tool_response("start_conversion", {"file_id": "objects/report.pdf", "target_format": "docx"}),
            text_response("Started it."),
        ]
    )
    files = [("report.pdf", "objects/report.pdf", minimal_pdf("hi"), USER_ID)]
    with assistant_app(
        str(tmp_path / "tool-convert.db"), seed_files=files, llm=llm
    ) as harness:
        frames = _frames(_chat(harness, "convert report.pdf to docx"))

    assert harness.conversions.converted
    call = harness.conversions.converted[0]
    assert call["source_format"] == "pdf"
    assert call["target_format"] == "docx"
    assert call["user_id"] == USER_ID
    assert call["object_key"] == "objects/report.pdf"

    done = frames[-1][1]
    assert [artifact["type"] for artifact in done["artifacts"]] == ["file", "job"]
    assert done["content"] == "Started it."


def test_the_model_cannot_convert_another_users_file(tmp_path) -> None:
    llm = FakeLlmPort(
        [
            tool_response("start_conversion", {"file_id": "objects/secret.pdf", "target_format": "docx"}),
            text_response("I could not do that."),
        ]
    )
    files = [("secret.pdf", "objects/secret.pdf", minimal_pdf("hi"), OTHER_USER_ID)]
    with assistant_app(
        str(tmp_path / "tool-owner.db"), seed_files=files, llm=llm
    ) as harness:
        frames = _frames(_chat(harness, "convert secret.pdf to docx"))

    assert harness.conversions.converted == []
    # The model is told, so it can explain, and the error frame is never used.
    assert "error" in llm.calls[1][0][-1].content
    assert frames[-1][1]["content"] == "I could not do that."
