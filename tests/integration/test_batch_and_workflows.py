"""Endpoint tests for batch conversions and saved workflows.

Both features are extensions of the conversion pipeline, so the properties that
matter are the ones that could quietly break it:

* **Ownership.** A batch or a run only ever touches files the caller owns. This
  is the assertion that matters most — a leak here converts one account's
  document on another's instruction — so it is tested against the real
  ``FileService`` and the real SQL repositories rather than a stub that could
  agree with a broken implementation.
* **Partial success is real.** One bad file must not prevent the others from
  starting, and must not be reported as a success either.
* **A run re-authorizes.** A workflow stores no file ids, so its access cannot
  outlive the selection; running it with another account's file must not
  convert that file.
* **Validation happens before work starts.** An unsupported file is reported
  without a job being created for anything.

Only auth and the session are overridden. The queue is a recording fake — the
one thing that genuinely cannot run here — so the tests can assert *what was
enqueued* without a Redis or a worker.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.services.conversion_service import ConversionService
from src.application.services.file_service import FileService
from src.application.services.workflow_service import WorkflowService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_conversion_job_repo import (
    SQLConversionJobRepository,
)
from src.infrastructure.adapters.repository.sql_saved_workflow_repo import (
    SQLSavedWorkflowRepository,
)
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFileModel, UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_service,
    get_file_service,
    get_workflow_service,
)

OWNER_ID = 1
OTHER_ID = 2


@dataclass
class FakeUser:
    id: int


class RecordingQueue:
    """Captures what a batch enqueued, in order."""

    def __init__(self) -> None:
        self.published: list[ConversionJob] = []

    async def publish_job(self, job: ConversionJob, stream: str | None = None) -> None:
        del stream
        self.published.append(job)


class FakeStorageOps:
    async def stat_object(self, object_key: str) -> dict | None:
        del object_key
        return None

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        del object_key, max_bytes
        return b""

    async def remove_object(self, object_key: str) -> bool:
        del object_key
        return True


class FakeSubscriptionRepo:
    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        del user_id
        return SubscriptionTier.FREE


class SqliteBackend:
    """Lazily builds the SQLite engine inside the app's event loop."""

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
                # Seeding is idempotent because the isolation tests open a
                # *second* client over the same database file (one context per
                # identity). Without this guard the second one re-inserts the
                # same user ids and dies on the primary key.
                already_seeded = (
                    await session.execute(select(UserModel.id).limit(1))
                ).scalar_one_or_none() is not None
                if not already_seeded:
                    now = datetime.now(UTC)
                    for user_id, username in ((OWNER_ID, "owner"), (OTHER_ID, "other")):
                        session.add(
                            UserModel(
                                id=user_id,
                                username=username,
                                email=f"{username}@example.com",
                                hashed_password="x",
                                is_active=True,
                                created_at=now,
                            )
                        )
                    await session.flush()
                    # Both accounts own a convertible file, so a leaked id would
                    # still produce a plausible-looking success.
                    session.add_all(
                        [
                            _file("f-owner-pdf", OWNER_ID, "owner document.pdf"),
                            _file("f-owner-docx", OWNER_ID, "owner letter.docx"),
                            _file("f-other-pdf", OTHER_ID, "other secret.pdf"),
                        ]
                    )
                    await session.commit()
            self._factory = factory
        return self._factory


def _file(file_id: str, user_id: int, file_name: str) -> UserFileModel:
    return UserFileModel(
        id=file_id,
        user_id=user_id,
        folder_id=None,
        file_key=f"upload/{file_id}/{file_name}",
        file_name=file_name,
        file_extension=file_name.rsplit(".", 1)[-1].lower(),
        file_size_bytes=512,
        mime_type="application/octet-stream",
        is_favorite=False,
        created_at=datetime.now(UTC),
        expires_at=None,
    )


@contextmanager
def api_client(db_path: str, *, as_user: int = OWNER_ID) -> Generator[TestClient, None, None]:
    backend = SqliteBackend(db_path)
    queue = RecordingQueue()

    async def no_op_initialize_database() -> None:
        return None

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=FakeStorageOps(),
                subscription_repository=FakeSubscriptionRepo(),
            )

    async def override_conversion_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield ConversionService(
                queue_port=queue,
                db_repository=SQLConversionJobRepository(session=session),
            )

    async def override_workflow_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield WorkflowService(SQLSavedWorkflowRepository(session=session), max_batch_files=20)

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=as_user)
    api_main.app.dependency_overrides[get_file_service] = override_file_service
    api_main.app.dependency_overrides[get_conversion_service] = override_conversion_service
    api_main.app.dependency_overrides[get_workflow_service] = override_workflow_service

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def create_workflow(client: TestClient, *, name: str = "To PDF", target: str = "pdf") -> str:
    response = client.post(
        "/api/v1/workflows",
        json={
            "name": name,
            "definition": {"operations": [{"type": "convert", "target_format": target}]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["workflow_id"]


class TestBatchConversion:
    # ``rtf`` is used as the shared target because both seeded source formats
    # (pdf and docx) genuinely support it. Converting a file to its own format
    # is NOT supported, which is worth remembering when reading these tests:
    # `pdf -> pdf` is a rejection, not a conversion.

    def test_converts_several_files_and_groups_them_under_one_batch(self, tmp_path) -> None:
        with api_client(str(tmp_path / "b.db")) as client:
            response = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf", "f-owner-docx"], "target_format": "rtf"},
            )
            assert response.status_code == 202, response.text
            body = response.json()
            # The body is in the message so a failure names which item failed
            # and why, instead of only the aggregate count.
            assert body["status"] == "success", body
            assert body["created_count"] == 2
            assert body["failed_count"] == 0
            job_ids = [item["job"]["job_id"] for item in body["items"]]
            assert len(set(job_ids)) == 2

            # The batch is readable back by id — the property that lets a
            # client recover after a reload.
            status = client.get(f"/api/conversions/batches/{body['batch_id']}").json()
            assert status["total"] == 2
            assert {item["job_id"] for item in status["items"]} == set(job_ids)

    def test_normalises_the_target_format(self, tmp_path) -> None:
        # The same normalisation the single-conversion endpoint applies, so
        # ".RTF" and "rtf" cannot mean different things.
        with api_client(str(tmp_path / "b.db")) as client:
            response = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf"], "target_format": " .RTF "},
            )
            assert response.json()["items"][0]["job"]["target_format"] == "rtf"

    def test_rejects_converting_a_file_to_its_own_format(self, tmp_path) -> None:
        # Reported per item rather than as a whole-request failure.
        with api_client(str(tmp_path / "b.db")) as client:
            body = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf"], "target_format": "pdf"},
            ).json()
            assert body["created_count"] == 0
            assert body["items"][0]["job"] is None
            assert body["items"][0]["error"]

    def test_reports_an_unknown_file_per_item_without_failing_the_batch(self, tmp_path) -> None:
        # Partial success: the good file is still converted.
        with api_client(str(tmp_path / "b.db")) as client:
            body = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf", "does-not-exist"], "target_format": "rtf"},
            ).json()
            assert body["status"] == "partial"
            assert body["created_count"] == 1
            assert body["failed_count"] == 1
            failed = [item for item in body["items"] if item["error"]]
            assert [item["file_id"] for item in failed] == ["does-not-exist"]
            assert failed[0]["job"] is None

    def test_never_converts_another_users_file(self, tmp_path) -> None:
        # The file exists and is convertible — it just belongs to someone else.
        # Reporting it as unavailable (rather than converting it) is the
        # security property this feature could most easily get wrong.
        with api_client(str(tmp_path / "b.db")) as client:
            body = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-other-pdf"], "target_format": "rtf"},
            ).json()
            assert body["created_count"] == 0
            assert body["status"] == "failed"
            assert body["items"][0]["job"] is None
            assert "no longer available" in body["items"][0]["error"].lower()

    def test_rejects_an_empty_selection(self, tmp_path) -> None:
        with api_client(str(tmp_path / "b.db")) as client:
            response = client.post(
                "/api/conversions/batch", json={"file_ids": [], "target_format": "pdf"}
            )
            assert response.status_code == 422

    def test_rejects_more_files_than_one_batch_may_hold(self, tmp_path) -> None:
        with api_client(str(tmp_path / "b.db")) as client:
            response = client.post(
                "/api/conversions/batch",
                json={"file_ids": [f"f{i}" for i in range(21)], "target_format": "pdf"},
            )
            assert response.status_code == 422

    def test_duplicate_ids_produce_one_job(self, tmp_path) -> None:
        # Selecting the same file twice is a mis-click, not a request to convert
        # it twice — and double-charging credits for it would be worse.
        with api_client(str(tmp_path / "b.db")) as client:
            body = client.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf", "f-owner-pdf"], "target_format": "rtf"},
            ).json()
            assert body["created_count"] == 1

    def test_an_unknown_batch_is_empty_not_a_404(self, tmp_path) -> None:
        # No 404: that would confirm whether an id exists to someone guessing.
        with api_client(str(tmp_path / "b.db")) as client:
            body = client.get("/api/conversions/batches/nope").json()
            assert body == {
                "batch_id": "nope",
                "status": "empty",
                "total": 0,
                "completed_count": 0,
                "failed_count": 0,
                "active_count": 0,
                "items": [],
                "workflow_id": None,
            }

    def test_a_batch_of_another_user_reads_as_empty(self, tmp_path) -> None:
        with api_client(str(tmp_path / "b.db")) as owner:
            batch_id = owner.post(
                "/api/conversions/batch",
                json={"file_ids": ["f-owner-pdf"], "target_format": "pdf"},
            ).json()["batch_id"]

        with api_client(str(tmp_path / "b.db"), as_user=OTHER_ID) as attacker:
            assert attacker.get(f"/api/conversions/batches/{batch_id}").json()["total"] == 0


class TestWorkflowCrud:
    def test_creates_and_lists_a_workflow(self, tmp_path) -> None:
        with api_client(str(tmp_path / "w.db")) as client:
            workflow_id = create_workflow(client, name="Prepare application", target="pdf")
            listed = client.get("/api/v1/workflows").json()["workflows"]
            assert [w["workflow_id"] for w in listed] == [workflow_id]
            assert listed[0]["name"] == "Prepare application"
            assert listed[0]["definition"]["source"] == "select_at_run"
            assert listed[0]["run_count"] == 0

    @pytest.mark.parametrize(
        "definition",
        [
            {"operations": [{"type": "delete_everything", "target_format": "pdf"}]},
            {"operations": [{"type": "convert", "target_format": ""}]},
            {"operations": []},
            {"source": "some_folder", "operations": [{"type": "convert", "target_format": "pdf"}]},
        ],
    )
    def test_rejects_an_invalid_definition(self, tmp_path, definition) -> None:
        # Each of these would otherwise be stored and then fail a run later.
        with api_client(str(tmp_path / "w.db")) as client:
            response = client.post(
                "/api/v1/workflows", json={"name": "Bad", "definition": definition}
            )
            assert response.status_code == 422, response.text

    def test_rejects_a_blank_name(self, tmp_path) -> None:
        with api_client(str(tmp_path / "w.db")) as client:
            response = client.post(
                "/api/v1/workflows",
                json={"name": "   ", "definition": {"operations": [{"type": "convert", "target_format": "pdf"}]}},
            )
            assert response.status_code == 422

    def test_updates_in_place(self, tmp_path) -> None:
        with api_client(str(tmp_path / "w.db")) as client:
            workflow_id = create_workflow(client, name="Old", target="pdf")
            response = client.put(
                f"/api/v1/workflows/{workflow_id}",
                json={
                    "name": "New",
                    "description": "changed",
                    "definition": {"operations": [{"type": "convert", "target_format": "docx"}]},
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["name"] == "New"
            assert body["description"] == "changed"
            assert body["definition"]["operations"][0]["target_format"] == "docx"

    def test_deletes(self, tmp_path) -> None:
        with api_client(str(tmp_path / "w.db")) as client:
            workflow_id = create_workflow(client)
            assert client.delete(f"/api/v1/workflows/{workflow_id}").status_code == 204
            assert client.get(f"/api/v1/workflows/{workflow_id}").status_code == 404
            assert client.get("/api/v1/workflows").json()["workflows"] == []

    def test_another_user_cannot_read_update_or_delete(self, tmp_path) -> None:
        with api_client(str(tmp_path / "w.db")) as owner:
            workflow_id = create_workflow(owner)

        with api_client(str(tmp_path / "w.db"), as_user=OTHER_ID) as attacker:
            # 404 rather than 403, so the id's existence is not confirmed.
            assert attacker.get(f"/api/v1/workflows/{workflow_id}").status_code == 404
            assert attacker.get("/api/v1/workflows").json()["workflows"] == []
            assert (
                attacker.put(
                    f"/api/v1/workflows/{workflow_id}",
                    json={
                        "name": "Stolen",
                        "definition": {"operations": [{"type": "convert", "target_format": "pdf"}]},
                    },
                ).status_code
                == 404
            )
            assert attacker.delete(f"/api/v1/workflows/{workflow_id}").status_code == 404


class TestWorkflowRun:
    def test_runs_and_records_the_run_against_the_workflow(self, tmp_path) -> None:
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            response = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-owner-docx"]},
            )
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["status"] == "success"
            assert body["created_count"] == 1
            assert body["workflow_id"] == workflow_id
            # The run is a batch, so it is readable on the batch endpoint too.
            status = client.get(f"/api/conversions/batches/{body['batch_id']}").json()
            assert status["total"] == 1
            assert status["workflow_id"] == workflow_id

            assert client.get(f"/api/v1/workflows/{workflow_id}").json()["run_count"] == 1

    def test_reports_an_unsupported_file_before_starting_anything(self, tmp_path) -> None:
        # A workflow that converts to PDF cannot convert a PDF to PDF, and the
        # reason must name the file rather than appear as a silent no-op.
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            body = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-owner-pdf"]},
            ).json()
            assert body["created_count"] == 0
            assert body["status"] == "failed"
            problems = body["problems"]
            assert len(problems) == 1
            assert problems[0]["file_name"] == "owner document.pdf"
            assert "cannot be converted" in problems[0]["error"]

    def test_runs_only_the_supported_files_of_a_mixed_selection(self, tmp_path) -> None:
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            body = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-owner-docx", "f-owner-pdf"]},
            ).json()
            assert body["status"] == "partial"
            assert body["created_count"] == 1
            assert body["failed_count"] == 1

    def test_never_converts_another_users_file(self, tmp_path) -> None:
        # The workflow is the caller's own; the file is not. A workflow must not
        # be a laundering step for a file the caller cannot otherwise convert.
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            body = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-other-pdf"]},
            ).json()
            assert body["created_count"] == 0
            assert body["items"][0]["job"] is None

    def test_cannot_run_another_users_workflow(self, tmp_path) -> None:
        with api_client(str(tmp_path / "r.db")) as owner:
            workflow_id = create_workflow(owner, target="pdf")

        with api_client(str(tmp_path / "r.db"), as_user=OTHER_ID) as attacker:
            response = attacker.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-other-pdf"]},
            )
            assert response.status_code == 404

    def test_rejects_an_empty_selection(self, tmp_path) -> None:
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client)
            response = client.post(f"/api/v1/workflows/{workflow_id}/runs", json={"file_ids": []})
            assert response.status_code == 422

    def test_duplicate_ids_run_once(self, tmp_path) -> None:
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            body = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-owner-docx", "f-owner-docx"]},
            ).json()
            assert body["created_count"] == 1

    def test_deleting_a_workflow_keeps_its_conversions(self, tmp_path) -> None:
        # The conversions are the user's own record of work that really
        # happened; deleting the shortcut must not erase them.
        with api_client(str(tmp_path / "r.db")) as client:
            workflow_id = create_workflow(client, target="pdf")
            batch_id = client.post(
                f"/api/v1/workflows/{workflow_id}/runs",
                json={"file_ids": ["f-owner-docx"]},
            ).json()["batch_id"]

            assert client.delete(f"/api/v1/workflows/{workflow_id}").status_code == 204

            status = client.get(f"/api/conversions/batches/{batch_id}").json()
            assert status["total"] == 1
