"""End-to-end tests for the cloud-workspace API, in process.

These drive the real FastAPI app, the real routers and the real SQL
repositories, overriding only two things that genuinely cannot run here:

* the **runtime provider** — no Docker daemon is available, so the fake models
  the provider's observable behaviour; and
* the **queue** — a recording fake, so the tests can assert *what was enqueued*
  without a Redis or a worker.

The properties that matter are the contract ones a client depends on: status
codes, response shapes, ownership, and the fact that a durable record exists
before anything is signalled.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.services.workspaces.execution_service import ExecutionService
from src.application.services.workspaces.workspace_service import WorkspaceService
from src.infrastructure.adapters.repository.sql_execution_repo import (
    SQLExecutionRepository,
)
from src.infrastructure.adapters.repository.sql_workspace_repo import (
    SQLRuntimeImageRevisionRepository,
    SQLRuntimeRepository,
    SQLWorkspaceRepository,
)
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.workspace_dependencies import (
    get_execution_service,
    get_workspace_service,
)
from tests.fakes.fake_runtime_provider import FakeRuntimeProvider
from tests.fakes.fake_workspace_queue import FakeWorkspaceQueue

OWNER_ID = 11
OTHER_ID = 12


@dataclass
class FakeUser:
    id: int


@contextmanager
def workspace_client(
    db_path: str,
    *,
    user_id: int = OWNER_ID,
) -> Generator[tuple[TestClient, FakeRuntimeProvider, FakeWorkspaceQueue], None, None]:
    """A TestClient with the workspace dependencies overridden onto SQLite."""
    provider = FakeRuntimeProvider()
    queue = FakeWorkspaceQueue()
    factory: async_sessionmaker | None = None

    async def _factory() -> async_sessionmaker:
        nonlocal factory
        if factory is None:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                if (await session.scalar(select(UserModel.id).limit(1))) is None:
                    session.add(
                        UserModel(
                            id=OWNER_ID,
                            username="owner",
                            email="owner@example.com",
                            hashed_password="x",
                            is_active=True,
                        )
                    )
                    session.add(
                        UserModel(
                            id=OTHER_ID,
                            username="other",
                            email="other@example.com",
                            hashed_password="x",
                            is_active=True,
                        )
                    )
                    await session.commit()
        return factory

    def _build(session) -> tuple[WorkspaceService, ExecutionService]:
        """Assemble the real services over one request-scoped SQLite session."""
        workspaces = SQLWorkspaceRepository(session=session)
        runtimes = SQLRuntimeRepository(session=session)
        revisions = SQLRuntimeImageRevisionRepository(session=session)
        executions = SQLExecutionRepository(session=session)
        return (
            WorkspaceService(
                workspace_repository=workspaces,
                runtime_repository=runtimes,
                revision_repository=revisions,
                execution_repository=executions,
                runtime_provider=provider,
                queue=queue,
            ),
            ExecutionService(
                execution_repository=executions,
                workspace_repository=workspaces,
                queue=queue,
            ),
        )

    async def _workspace_service():
        session = (await _factory())()
        try:
            service, _ = _build(session)
            yield service
        finally:
            # Closing the session in the generator's finally keeps the pool
            # honest: without it the garbage collector reclaims live SQLite
            # connections and SQLAlchemy warns on every test.
            await session.close()

    async def _execution_service():
        session = (await _factory())()
        try:
            _, service = _build(session)
            yield service
        finally:
            await session.close()

    original_init = api_main.initialize_database

    async def _noop() -> None:
        return None

    api_main.initialize_database = _noop  # type: ignore[assignment]
    try:
        with TestClient(api_main.app) as client:
            overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
            saved = dict(overrides)
            overrides[get_current_user] = lambda: FakeUser(id=user_id)
            overrides[get_workspace_service] = _workspace_service
            overrides[get_execution_service] = _execution_service
            try:
                yield client, provider, queue
            finally:
                # Restore rather than clear: these tests nest two clients over
                # the same app (one per identity), and clearing would strip the
                # outer client's overrides too.
                overrides.clear()
                overrides.update(saved)
    finally:
        api_main.initialize_database = original_init


@pytest.fixture(name="client_bundle")
def client_bundle_fixture(tmp_path):
    with workspace_client(str(tmp_path / "workspaces.sqlite")) as bundle:
        yield bundle


def _create(client: TestClient, name: str = "Analysis") -> dict:
    response = client.post("/api/v1/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


class TestWorkspaceCrud:
    def test_create_returns_the_workspace_with_defaults(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        body = _create(client)

        assert body["state"] == "ACTIVE"
        assert body["execution_count"] == 0
        assert body["runtime"] is None
        assert body["runtime_config"]["python_version"] == "3.14"
        assert body["runtime_config"]["image_ref"] == "transform-python-runtime:1.0.0-py3.14"
        assert body["runtime_config"]["resource_profile"] == "standard"

    def test_a_blank_name_is_a_validation_error(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        response = client.post("/api/v1/workspaces", json={"name": "   "})
        # The schema accepts a whitespace name; the domain refuses it, and the
        # API surfaces that as a 4xx rather than a 500.
        assert response.status_code in (422, 400)

    def test_an_unknown_field_is_rejected(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        response = client.post(
            "/api/v1/workspaces", json={"name": "x", "provider": "e2b"}
        )
        assert response.status_code == 422

    def test_listing_is_scoped_to_the_caller(self, client_bundle, tmp_path) -> None:
        client, _provider, _queue = client_bundle
        _create(client)

        with workspace_client(str(tmp_path / "workspaces.sqlite"), user_id=OTHER_ID) as (
            other_client,
            _p,
            _q,
        ):
            listing = other_client.get("/api/v1/workspaces")
            assert listing.status_code == 200
            assert listing.json()["total"] == 0

    def test_another_users_workspace_is_a_404(self, client_bundle, tmp_path) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)

        with workspace_client(str(tmp_path / "workspaces.sqlite"), user_id=OTHER_ID) as (
            other_client,
            _p,
            _q,
        ):
            assert (
                other_client.get(
                    f"/api/v1/workspaces/{created['workspace_id']}"
                ).status_code
                == 404
            )

    def test_patch_renames(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.patch(
            f"/api/v1/workspaces/{created['workspace_id']}", json={"name": "Renamed"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"

    def test_delete_removes_it(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        assert (
            client.delete(f"/api/v1/workspaces/{created['workspace_id']}").status_code
            == 204
        )
        assert (
            client.get(f"/api/v1/workspaces/{created['workspace_id']}").status_code == 404
        )

    def test_authentication_is_required(self, tmp_path) -> None:
        """With no override, the real dependency runs and refuses."""
        with TestClient(api_main.app) as client:
            assert client.get("/api/v1/workspaces").status_code in (401, 403)


class TestRuntimeEndpoints:
    def test_a_first_start_is_202_and_a_second_is_200_unchanged(
        self, client_bundle
    ) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]

        first = client.post(f"/api/v1/workspaces/{workspace_id}/runtime/start")
        assert first.status_code == 202, first.text
        assert first.json()["state"] == "RUNNING"

        second = client.post(f"/api/v1/workspaces/{workspace_id}/runtime/start")
        assert second.status_code == 200
        assert second.json()["runtime_id"] == first.json()["runtime_id"]

    def test_stopping_without_a_runtime_is_a_conflict(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.post(f"/api/v1/workspaces/{created['workspace_id']}/runtime/stop")
        assert response.status_code == 409

    def test_stop_then_inspect_reports_stopped(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]
        client.post(f"/api/v1/workspaces/{workspace_id}/runtime/start")

        stopped = client.post(f"/api/v1/workspaces/{workspace_id}/runtime/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["state"] == "STOPPED"

        inspected = client.get(f"/api/v1/workspaces/{workspace_id}/runtime")
        assert inspected.json()["state"] == "STOPPED"

    def test_the_configuration_endpoint_reports_provider_capabilities(
        self, client_bundle
    ) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.get(
            f"/api/v1/workspaces/{created['workspace_id']}/runtime/configuration"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["config"]["uv_version"]
        assert body["provider"]["provider"] == "fake"
        assert body["supported_resource_profiles"] == ["large", "small", "standard"]
        assert body["profile"]["memory_mb"] > 0

    def test_archiving_refuses_new_work(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]
        assert client.post(f"/api/v1/workspaces/{workspace_id}/archive").status_code == 200

        submitted = client.post(
            f"/api/v1/workspaces/{workspace_id}/executions",
            json={"mode": "SCRIPT", "source": "print(1)"},
        )
        assert submitted.status_code == 409

        assert (
            client.post(f"/api/v1/workspaces/{workspace_id}/reactivate").status_code
            == 200
        )


class TestExecutions:
    def test_submitting_records_and_queues_the_run(self, client_bundle) -> None:
        client, _provider, queue = client_bundle
        created = _create(client)

        response = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "SCRIPT", "source": "print('hello')"},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "QUEUED"
        assert body["settled"] is False
        assert body["exit_code"] is None
        assert len(queue.executions) == 1

        fetched = client.get(f"/api/v1/executions/{body['execution_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["source"] == "print('hello')"

    def test_an_idempotency_key_returns_the_same_execution(self, client_bundle) -> None:
        client, _provider, queue = client_bundle
        created = _create(client)
        payload = {
            "mode": "SCRIPT",
            "source": "print(1)",
            "idempotency_key": "abc",
        }
        first = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions", json=payload
        ).json()
        second = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions", json=payload
        ).json()
        assert first["execution_id"] == second["execution_id"]
        assert len(queue.executions) == 1

    def test_an_unknown_mode_is_a_422(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "SHELL", "source": "ls"},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("source", ["install", "add --index-url http://evil"])
    def test_a_rejected_project_operation_is_a_422(
        self, client_bundle, source: str
    ) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "PROJECT", "source": source},
        )
        assert response.status_code == 422, response.text

    def test_an_over_long_timeout_is_a_422(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "SCRIPT", "source": "print(1)", "timeout_seconds": 999_999},
        )
        assert response.status_code == 422

    def test_cancelling_twice_is_idempotent(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        execution = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "SCRIPT", "source": "print(1)"},
        ).json()

        first = client.post(f"/api/v1/executions/{execution['execution_id']}/cancel")
        assert first.status_code == 200
        assert first.json()["status"] == "CANCELLED"
        assert first.json()["settled"] is True

        second = client.post(f"/api/v1/executions/{execution['execution_id']}/cancel")
        assert second.status_code == 200
        assert second.json()["status"] == "CANCELLED"

    def test_history_is_paginated(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        for index in range(3):
            client.post(
                f"/api/v1/workspaces/{created['workspace_id']}/executions",
                json={"mode": "SCRIPT", "source": f"print({index})"},
            )
        response = client.get(
            f"/api/v1/workspaces/{created['workspace_id']}/executions?page=1&page_size=2"
        )
        body = response.json()
        assert body["total"] == 3
        assert len(body["executions"]) == 2

    def test_another_users_execution_is_a_404(self, client_bundle, tmp_path) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        execution = client.post(
            f"/api/v1/workspaces/{created['workspace_id']}/executions",
            json={"mode": "SCRIPT", "source": "print(1)"},
        ).json()

        with workspace_client(str(tmp_path / "workspaces.sqlite"), user_id=OTHER_ID) as (
            other_client,
            _p,
            _q,
        ):
            assert (
                other_client.get(
                    f"/api/v1/executions/{execution['execution_id']}"
                ).status_code
                == 404
            )


class TestFilesAndEnvironment:
    def test_files_require_a_runtime(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.get(f"/api/v1/workspaces/{created['workspace_id']}/files")
        assert response.status_code == 409, response.text

    def test_files_can_be_listed_read_and_written_once_running(self, client_bundle) -> None:
        client, provider, _queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]
        client.post(f"/api/v1/workspaces/{workspace_id}/runtime/start")
        provider.seed_file(workspace_id, "analysis/main.py", b"print('hi')\n")

        listing = client.get(f"/api/v1/workspaces/{workspace_id}/files")
        assert listing.status_code == 200, listing.text
        names = {entry["name"] for entry in listing.json()["entries"]}
        assert "analysis" in names

        read = client.get(
            f"/api/v1/workspaces/{workspace_id}/files/content?path=analysis/main.py"
        )
        assert read.status_code == 200, read.text
        assert read.json()["content"] == "print('hi')\n"
        assert read.json()["encoding"] == "utf-8"

        written = client.put(
            f"/api/v1/workspaces/{workspace_id}/files/content",
            json={"path": "notes.txt", "content": "hello"},
        )
        assert written.status_code == 204, written.text
        assert provider.workspace_files[workspace_id]["notes.txt"] == b"hello"

    def test_environment_requires_a_running_runtime_to_save(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]
        assert (
            client.post(f"/api/v1/workspaces/{workspace_id}/environment/save").status_code
            == 409
        )

    def test_environment_reads_and_saves(self, client_bundle) -> None:
        client, _provider, queue = client_bundle
        created = _create(client)
        workspace_id = created["workspace_id"]
        client.post(f"/api/v1/workspaces/{workspace_id}/runtime/start")

        read = client.get(f"/api/v1/workspaces/{workspace_id}/environment")
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["config"]["python_version"] == "3.14"
        assert body["revisions"] == []
        # The server states what survives a stop rather than leaving it implied.
        assert "uv.lock" in body["persistence_note"]

        saved = client.post(f"/api/v1/workspaces/{workspace_id}/environment/save")
        assert saved.status_code == 202, saved.text
        assert saved.json()["status"] == "PENDING"
        assert len(queue.snapshots) == 1

        after = client.get(f"/api/v1/workspaces/{workspace_id}/environment").json()
        assert len(after["revisions"]) == 1

    def test_artifacts_start_empty(self, client_bundle) -> None:
        client, _provider, _queue = client_bundle
        created = _create(client)
        response = client.get(f"/api/v1/workspaces/{created['workspace_id']}/artifacts")
        assert response.status_code == 200
        assert response.json()["artifacts"] == []
