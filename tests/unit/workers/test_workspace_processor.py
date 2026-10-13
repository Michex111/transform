"""The workspace worker's processing rules, without a daemon or a queue.

These are the decisions that decide whether a user can trust the pipeline:

* a queued execution really runs, and its output and exit code are recorded;
* a **redelivered** message does not run the work twice (the queue can and does
  deliver twice after a crashed worker's entry is reclaimed);
* a cancellation that arrives **while the program is running** wins — the run's
  outcome must not overwrite it;
* a timeout is a distinct terminal state from a failure;
* a snapshot of a runtime that is not up fails visibly rather than staying
  PENDING forever.

The runtime provider is the fake, and the repositories are the real SQL ones over
SQLite — the claim and the terminal-state guard are adapter behaviour, and a fake
repository would agree with a broken implementation.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.ports.runtime_provider_port import ExecutionOutcome
from src.application.services.workspace_service import WorkspaceService
from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus
from src.infrastructure.adapters.queues.redis_stream_workspace_queue import (
    WORK_KIND_EXECUTION,
    WORK_KIND_SNAPSHOT,
    WorkspaceWorkMessage,
)
from src.infrastructure.adapters.repository.sql_execution_repo import (
    SQLExecutionRepository,
)
from src.infrastructure.adapters.repository.sql_workspace_repo import (
    SQLRuntimeImageRevisionRepository,
    SQLRuntimeRepository,
    SQLWorkspaceRepository,
)
from tests.fakes.fake_runtime_provider import FakeRuntimeProvider
from tests.fakes.fake_workspace_queue import FakeWorkspaceQueue
from tests.fixtures.workspace_repos import WorkspaceRepos, build_repos, dispose_repos
from workers.workspace_worker.processor import handle_execution, handle_snapshot, process_work

USER = 7


class FakeArtifactStorage:
    """Records uploads through the path-based storage gateway."""

    def __init__(self, *, fail: bool = False) -> None:
        self.uploaded: dict[str, bytes] = {}
        self._fail = fail

    def upload(self, target_key: str, source_path: Any) -> None:
        if self._fail:
            raise RuntimeError("bucket unreachable")
        with open(source_path, "rb") as handle:
            self.uploaded[target_key] = handle.read()


class FakeSettings:
    BASE_TARGET_KEY = "s3-file_store"


class FakeContext:
    """The duck-typed surface ``process_work`` needs from the worker context."""

    def __init__(
        self,
        repos: WorkspaceRepos,
        provider: FakeRuntimeProvider,
        queue: FakeWorkspaceQueue,
        *,
        snapshot_retention: int = 2,
        storage: Any = None,
        max_artifacts: int = 20,
        max_artifact_bytes: int = 1024 * 1024,
    ) -> None:
        self.repos = repos
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=repos.engine, expire_on_commit=False
        )
        self.provider = provider
        self.queue = queue
        self.execution_timeout_seconds = 30
        self.max_output_bytes = 4096
        self.snapshot_retention = snapshot_retention
        self.storage = storage
        self.max_artifacts_per_execution = max_artifacts
        self.max_artifact_bytes = max_artifact_bytes
        self.artifact_directory = "outputs"
        self.settings = FakeSettings()

    def build_service(self, session: Any) -> WorkspaceService:
        return WorkspaceService(
            workspace_repository=SQLWorkspaceRepository(session=session),
            runtime_repository=SQLRuntimeRepository(session=session),
            revision_repository=SQLRuntimeImageRevisionRepository(session=session),
            execution_repository=SQLExecutionRepository(session=session),
            runtime_provider=self.provider,
            queue=self.queue,
            snapshot_retention=self.snapshot_retention,
        )


class CancellingProvider(FakeRuntimeProvider):
    """A provider that cancels the execution while it is "running"."""

    def __init__(self, on_execute) -> None:
        super().__init__()
        self._on_execute = on_execute

    async def execute(self, provider_ref, request):  # type: ignore[override]
        await self._on_execute()
        return await super().execute(provider_ref, request)


async def _setup(
    tmp_path: Path,
    provider: FakeRuntimeProvider | None = None,
    *,
    retention: int = 2,
    storage: Any = None,
    max_artifact_bytes: int = 1024 * 1024,
) -> tuple[FakeContext, str]:
    repos = await build_repos(tmp_path)
    context = FakeContext(
        repos,
        provider or FakeRuntimeProvider(),
        FakeWorkspaceQueue(),
        snapshot_retention=retention,
        storage=storage,
        max_artifact_bytes=max_artifact_bytes,
    )
    service = context.build_service(repos.session)
    workspace = await service.create_workspace(user_id=USER, name="ws")
    return context, workspace.workspace_id


async def _create_execution(
    context: FakeContext, workspace_id: str, source: str = "print(1)"
) -> Execution:
    execution = Execution(
        execution_id=f"ex-{source[:6]}-{id(source)}",
        workspace_id=workspace_id,
        user_id=USER,
        mode=ExecutionMode.SCRIPT,
        source=source,
    )
    await context.repos.executions.create_execution(execution)
    return execution


def _message(execution_id: str, workspace_id: str) -> WorkspaceWorkMessage:
    return WorkspaceWorkMessage(
        kind=WORK_KIND_EXECUTION, workspace_id=workspace_id, execution_id=execution_id
    )


class TestHandleExecution:
    def test_a_queued_execution_runs_and_records_its_result(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                context.provider.scripted_outcomes = [
                    ExecutionOutcome(exit_code=0, stdout="hello\n", duration_ms=42)
                ]
                execution = await _create_execution(context, workspace_id)

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert stored.status is ExecutionStatus.SUCCEEDED
                assert stored.exit_code == 0
                assert stored.stdout_excerpt == "hello\n"
                assert stored.duration_ms == 42
                assert stored.runtime_id is not None
                # The runtime was started, and this is the run that used it.
                assert len(context.provider.executed) == 1
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_the_runtime_is_started_once_for_two_executions(self, tmp_path: Path) -> None:
        """The worker goes through the API's service, so idempotency is shared."""

        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                first = await _create_execution(context, workspace_id, "print(1)")
                second = await _create_execution(context, workspace_id, "print(2)")

                await handle_execution(context, _message(first.execution_id, workspace_id))
                await handle_execution(context, _message(second.execution_id, workspace_id))

                assert context.provider.calls.count("create_runtime") == 1
                assert len(context.provider.executed) == 2
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_a_finished_execution_is_not_run_again(self, tmp_path: Path) -> None:
        """A reclaimed queue entry must not re-run a user's program."""

        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                execution = await _create_execution(context, workspace_id)
                await handle_execution(context, _message(execution.execution_id, workspace_id))
                assert len(context.provider.executed) == 1

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                assert len(context.provider.executed) == 1
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_a_cancellation_during_the_run_wins_over_the_outcome(self, tmp_path: Path) -> None:
        """The one race this pipeline cannot avoid, asserted directly."""

        async def scenario() -> None:
            repos = await build_repos(tmp_path)
            cancelled_once = {"done": False}

            async def cancel_it() -> None:
                # Cancel the row the way the API does, while the provider is
                # about to report success.
                async with async_sessionmaker(bind=repos.engine, expire_on_commit=False)() as session:
                    repo = SQLExecutionRepository(session=session)
                    execution = await repo.get_execution(execution_id)
                    assert execution is not None
                    execution.cancel()
                    await repo.update_execution(execution)
                cancelled_once["done"] = True

            provider = CancellingProvider(cancel_it)
            context = FakeContext(repos, provider, FakeWorkspaceQueue())
            service = context.build_service(repos.session)
            workspace = await service.create_workspace(user_id=USER, name="ws")
            execution = await _create_execution(context, workspace.workspace_id)
            execution_id = execution.execution_id

            try:
                await handle_execution(context, _message(execution_id, workspace.workspace_id))

                assert cancelled_once["done"] is True
                stored = await repos.executions.get_execution(execution_id)
                assert stored is not None
                # The cancellation is kept; the successful outcome is discarded.
                assert stored.status is ExecutionStatus.CANCELLED
            finally:
                await dispose_repos(repos)

        asyncio.run(scenario())

    def test_a_timeout_is_its_own_terminal_state(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                context.provider.scripted_outcomes = [
                    ExecutionOutcome(
                        exit_code=None, timed_out=True, stdout="partial", duration_ms=30_000
                    )
                ]
                execution = await _create_execution(context, workspace_id)
                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert stored.status is ExecutionStatus.TIMED_OUT
                assert stored.exit_code is None
                assert stored.stdout_excerpt == "partial"
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_a_provider_error_fails_the_execution_with_the_reason(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                context.provider.scripted_outcomes = [
                    ExecutionOutcome(exit_code=None, error="ProviderExecutionError: gone")
                ]
                execution = await _create_execution(context, workspace_id)
                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert stored.status is ExecutionStatus.FAILED
                assert "gone" in (stored.error or "")
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_artifacts_written_to_outputs_are_recorded(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                execution = await _create_execution(context, workspace_id)
                # The provider's runtime must exist before files can be seeded
                # into it, so start it, then add the file.
                service = context.build_service(context.repos.session)
                started = await service.start_runtime(workspace_id, USER)
                assert started.runtime.provider_ref is not None
                context.provider.seed_file(workspace_id, "outputs/report.csv", b"a,b\n1,2\n")

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert [artifact.path for artifact in stored.artifacts] == ["outputs/report.csv"]
                assert stored.artifacts[0].size_bytes == 8
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_an_artifact_is_copied_into_durable_storage(self, tmp_path: Path) -> None:
        """The property that makes an artifact outlive its runtime.

        A path alone becomes a dangling reference the moment the container is
        replaced, so the bytes are copied into the same object storage the rest
        of the product uses and the recorded key is what the API hands back.
        """

        async def scenario() -> None:
            storage = FakeArtifactStorage()
            context, workspace_id = await _setup(tmp_path, storage=storage)
            try:
                execution = await _create_execution(context, workspace_id)
                service = context.build_service(context.repos.session)
                started = await service.start_runtime(workspace_id, USER)
                assert started.runtime.provider_ref is not None
                context.provider.seed_file(workspace_id, "outputs/report.csv", b"a,b\n1,2\n")

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                artifact = stored.artifacts[0]
                assert artifact.object_key is not None
                # Namespaced by workspace and execution, so two runs of the same
                # script cannot overwrite each other's outputs.
                assert workspace_id in artifact.object_key
                assert execution.execution_id in artifact.object_key
                assert artifact.object_key.endswith("report.csv")
                assert storage.uploaded[artifact.object_key] == b"a,b\n1,2\n"
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_a_storage_failure_leaves_the_run_successful(self, tmp_path: Path) -> None:
        """Copying is best-effort: a run that produced output must not be
        reported as failed because the bucket was unreachable."""

        async def scenario() -> None:
            storage = FakeArtifactStorage(fail=True)
            context, workspace_id = await _setup(tmp_path, storage=storage)
            try:
                execution = await _create_execution(context, workspace_id)
                service = context.build_service(context.repos.session)
                started = await service.start_runtime(workspace_id, USER)
                assert started.runtime.provider_ref is not None
                context.provider.seed_file(workspace_id, "outputs/report.csv", b"a,b\n")

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert stored.status is ExecutionStatus.SUCCEEDED
                # Recorded without a key, which the API reports as not
                # downloadable rather than as a broken link.
                assert stored.artifacts[0].object_key is None
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_an_oversized_artifact_is_recorded_but_not_copied(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            storage = FakeArtifactStorage()
            context, workspace_id = await _setup(
                tmp_path, storage=storage, max_artifact_bytes=4
            )
            try:
                execution = await _create_execution(context, workspace_id)
                service = context.build_service(context.repos.session)
                started = await service.start_runtime(workspace_id, USER)
                assert started.runtime.provider_ref is not None
                context.provider.seed_file(workspace_id, "outputs/big.bin", b"x" * 100)

                await handle_execution(context, _message(execution.execution_id, workspace_id))

                stored = await context.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                assert stored.artifacts[0].path == "outputs/big.bin"
                assert stored.artifacts[0].object_key is None
                assert storage.uploaded == {}
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_an_execution_whose_row_vanished_is_skipped(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                await handle_execution(context, _message("does-not-exist", workspace_id))
                assert context.provider.executed == []
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())


class TestHandleSnapshot:
    async def _revision(self, context: FakeContext, workspace_id: str) -> RuntimeImageRevision:
        revision = RuntimeImageRevision(
            revision_id=f"rev-{len(await context.repos.revisions.list_revisions(workspace_id, limit=50))}",
            workspace_id=workspace_id,
            provider=context.provider.name,
            config_fingerprint="fp-1",
        )
        await context.repos.revisions.create_revision(revision)
        return revision

    def test_a_snapshot_requires_a_running_runtime(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                revision = await self._revision(context, workspace_id)
                await handle_snapshot(
                    context,
                    WorkspaceWorkMessage(
                        kind=WORK_KIND_SNAPSHOT,
                        workspace_id=workspace_id,
                        revision_id=revision.revision_id,
                    ),
                )
                stored = await context.repos.revisions.get_revision(
                    revision.revision_id, workspace_id
                )
                assert stored is not None
                # Not left PENDING: the UI must be able to stop saying "saving…".
                assert stored.status.value == "FAILED"
                assert "not running" in (stored.error or "")
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_a_snapshot_of_a_running_runtime_becomes_ready(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                service = context.build_service(context.repos.session)
                await service.start_runtime(workspace_id, USER)
                revision = await self._revision(context, workspace_id)

                await handle_snapshot(
                    context,
                    WorkspaceWorkMessage(
                        kind=WORK_KIND_SNAPSHOT,
                        workspace_id=workspace_id,
                        revision_id=revision.revision_id,
                    ),
                )

                stored = await context.repos.revisions.get_revision(
                    revision.revision_id, workspace_id
                )
                assert stored is not None
                assert stored.status.value == "READY"
                assert stored.provider_snapshot_ref
                assert stored.ready_at is not None
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())

    def test_old_snapshots_are_pruned_to_the_retention_limit(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path, retention=2)
            try:
                service = context.build_service(context.repos.session)
                await service.start_runtime(workspace_id, USER)

                for _ in range(3):
                    revision = await self._revision(context, workspace_id)
                    await handle_snapshot(
                        context,
                        WorkspaceWorkMessage(
                            kind=WORK_KIND_SNAPSHOT,
                            workspace_id=workspace_id,
                            revision_id=revision.revision_id,
                        ),
                    )

                remaining = await context.repos.revisions.list_revisions(
                    workspace_id, limit=50
                )
                ready = [item for item in remaining if item.status.value == "READY"]
                # Snapshots are images: the bound is what stops them accumulating
                # forever on a workspace used daily.
                assert len(ready) == 2
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())


class TestDispatch:
    def test_an_unknown_kind_is_an_error(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            context, workspace_id = await _setup(tmp_path)
            try:
                with pytest.raises(ValueError):
                    await process_work(
                        context,
                        WorkspaceWorkMessage(kind="teleport", workspace_id=workspace_id),
                    )
            finally:
                await dispose_repos(context.repos)

        asyncio.run(scenario())
