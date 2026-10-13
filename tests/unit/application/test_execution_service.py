"""Execution submission, cancellation and artifact listing.

The properties under test are the ones that decide whether a user can trust the
feature: a submit is durable before it is queued, a duplicate submit with a key
does not run twice, a cancelled queued run can never be claimed, and a queue
failure produces a visible failure rather than work that silently never runs.
"""

import asyncio
from pathlib import Path

import pytest

from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    WorkspaceQuotaExceededError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
)
from src.application.services.workspaces.execution_service import (
    MAX_EXECUTION_TIMEOUT_SECONDS,
    ExecutionNotFoundError,
    ExecutionService,
    InvalidExecutionRequestError,
    TooManyActiveExecutionsError,
)
from src.domain.workspaces.entities.execution import Execution, ExecutionArtifact
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus
from tests.fakes.fake_workspace_queue import FakeWorkspaceQueue
from tests.fixtures.workspace_repos import WorkspaceRepos, build_repos, dispose_repos

USER = 7


class Harness:
    def __init__(self, repos: WorkspaceRepos) -> None:
        self.repos = repos
        self.queue = FakeWorkspaceQueue()
        self.service = ExecutionService(
            execution_repository=repos.executions,
            workspace_repository=repos.workspaces,
            queue=self.queue,
        )
        self.workspace_id: str = ""

    async def setup_workspace(self, name: str = "ws") -> str:
        from src.application.services.workspaces.workspace_service import WorkspaceService
        from tests.fakes.fake_runtime_provider import FakeRuntimeProvider

        workspaces = WorkspaceService(
            workspace_repository=self.repos.workspaces,
            runtime_repository=self.repos.runtimes,
            revision_repository=self.repos.revisions,
            execution_repository=self.repos.executions,
            runtime_provider=FakeRuntimeProvider(),
            queue=self.queue,
        )
        workspace = await workspaces.create_workspace(user_id=USER, name=name)
        self.workspace_id = workspace.workspace_id
        return workspace.workspace_id


async def _harness(tmp_path: Path) -> Harness:
    harness = Harness(await build_repos(tmp_path))
    await harness.setup_workspace()
    return harness


class TestSubmit:
    def test_a_submit_is_durable_then_queued(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print('hi')",
                )

                assert execution.status is ExecutionStatus.QUEUED
                assert execution.exit_code is None
                # Durable first...
                stored = await harness.repos.executions.get_execution(execution.execution_id)
                assert stored is not None
                # ...then signalled.
                assert [item.execution_id for item in harness.queue.executions] == [
                    execution.execution_id
                ]

                # The workspace's usage metadata moved with it.
                workspace = await harness.repos.workspaces.get_workspace(
                    harness.workspace_id, USER
                )
                assert workspace is not None
                assert workspace.execution_count == 1
                assert workspace.last_activity_at is not None
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_an_idempotency_key_collapses_a_retried_submit(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                first = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                    idempotency_key="run-42",
                )
                second = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                    idempotency_key="run-42",
                )

                assert second.execution_id == first.execution_id
                # Queued exactly once, and counted exactly once.
                assert len(harness.queue.executions) == 1
                workspace = await harness.repos.workspaces.get_workspace(
                    harness.workspace_id, USER
                )
                assert workspace is not None
                assert workspace.execution_count == 1
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_without_a_key_two_identical_runs_are_two_runs(self, tmp_path: Path) -> None:
        """Defaulting to "collapse identical programs" would be a worse surprise."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                first = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                second = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                assert first.execution_id != second.execution_id
                assert len(harness.queue.executions) == 2
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_queue_failure_is_a_visible_failure(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                harness.queue.fail_executions = RuntimeError("redis is down")

                with pytest.raises(InvalidExecutionRequestError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                    )

                executions, total = (
                    await harness.repos.executions.list_executions_for_workspace(
                        harness.workspace_id, limit=10, offset=0
                    )
                )
                assert total == 1
                assert executions[0].status is ExecutionStatus.FAILED
                assert "queue" in (executions[0].error or "")
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_an_unknown_workspace_is_not_found(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(WorkspaceNotFoundError):
                    await harness.service.submit(
                        workspace_id="nope",
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_an_archived_workspace_refuses_new_work(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                from src.application.services.workspaces.workspace_service import WorkspaceService
                from tests.fakes.fake_runtime_provider import FakeRuntimeProvider

                workspaces = WorkspaceService(
                    workspace_repository=harness.repos.workspaces,
                    runtime_repository=harness.repos.runtimes,
                    revision_repository=harness.repos.revisions,
                    execution_repository=harness.repos.executions,
                    runtime_provider=FakeRuntimeProvider(),
                    queue=harness.queue,
                )
                await workspaces.archive_workspace(harness.workspace_id, USER)
                with pytest.raises(WorkspaceNotUsableError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestValidation:
    @pytest.mark.parametrize("source", ["", "   ", "\n\t"])
    def test_an_empty_source_is_refused(self, tmp_path: Path, source: str) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(InvalidExecutionRequestError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source=source,
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_nul_byte_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(InvalidExecutionRequestError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)\x00",
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    @pytest.mark.parametrize("timeout", [0, -1, MAX_EXECUTION_TIMEOUT_SECONDS + 1])
    def test_an_out_of_range_timeout_is_refused(self, tmp_path: Path, timeout: int) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(InvalidExecutionRequestError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                        timeout_seconds=timeout,
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    @pytest.mark.parametrize(
        "source",
        [
            "install",                     # not in the allow-list
            "add",                         # needs an argument
            "add --index-url http://evil", # option-shaped, not a package
            "sync extra",                  # takes no arguments
            "add /etc/passwd",             # not a package spec
            "add '; rm -rf /'",            # a shell fragment
        ],
    )
    def test_a_project_operation_outside_the_allow_list_is_refused(
        self, tmp_path: Path, source: str
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(InvalidExecutionRequestError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.PROJECT,
                        source=source,
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    @pytest.mark.parametrize("source", ["sync", "lock", "add requests", "add requests==2.32.3"])
    def test_a_supported_project_operation_is_accepted(
        self, tmp_path: Path, source: str
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.PROJECT,
                    source=source,
                )
                assert execution.mode is ExecutionMode.PROJECT
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_the_concurrency_cap_is_enforced(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                harness.service.max_active_executions = 1
                await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                with pytest.raises(TooManyActiveExecutionsError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(2)",
                    )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestReadsAndCancel:
    def test_reads_are_scoped_to_the_owner(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                assert (
                    await harness.service.get(execution.execution_id, USER)
                ).execution_id == execution.execution_id
                with pytest.raises(ExecutionNotFoundError):
                    await harness.service.get(execution.execution_id, 999)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_the_listing_is_newest_first_and_paginated(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                for index in range(3):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source=f"print({index})",
                    )
                page, total = await harness.service.list_for_workspace(
                    workspace_id=harness.workspace_id, user_id=USER, limit=2, offset=0
                )
                assert total == 3
                assert len(page) == 2
                newest, older = page[0].created_at, page[1].created_at
                assert newest is not None and older is not None
                assert newest >= older
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_cancelling_a_queued_run_prevents_it_being_claimed(self, tmp_path: Path) -> None:
        """Cancellation is a status change; the claim's WHERE clause does the rest."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                cancelled = await harness.service.cancel(execution.execution_id, USER)
                assert cancelled.status is ExecutionStatus.CANCELLED

                # The worker's atomic claim finds nothing to take.
                assert (
                    await harness.repos.executions.claim_execution(
                        execution.execution_id, "rt-1"
                    )
                    is False
                )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_cancelling_twice_is_idempotent(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                await harness.service.cancel(execution.execution_id, USER)
                again = await harness.service.cancel(execution.execution_id, USER)
                assert again.status is ExecutionStatus.CANCELLED
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_only_one_claim_winner_exists(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                first = await harness.repos.executions.claim_execution(
                    execution.execution_id, "rt-1"
                )
                second = await harness.repos.executions.claim_execution(
                    execution.execution_id, "rt-2"
                )
                assert (first, second) == (True, False)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestArtifacts:
    def test_artifacts_are_listed_with_their_execution(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                execution = Execution(
                    execution_id="ex-1",
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                execution.start(runtime_id="rt-1")
                execution.succeed(
                    exit_code=0,
                    artifacts=[
                        ExecutionArtifact(path="out/report.csv", size_bytes=12),
                        ExecutionArtifact(path="out/plot.png", size_bytes=34),
                    ],
                )
                await harness.repos.executions.create_execution(execution)
                await harness.repos.executions.update_execution(execution)

                listed = await harness.service.list_artifacts(
                    workspace_id=harness.workspace_id, user_id=USER
                )
                assert {ref.artifact.path for ref in listed} == {
                    "out/report.csv",
                    "out/plot.png",
                }
                assert {ref.execution_id for ref in listed} == {"ex-1"}
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_an_unknown_workspace_is_not_found(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                with pytest.raises(WorkspaceNotFoundError):
                    await harness.service.list_artifacts(workspace_id="nope", user_id=USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestPolicyGates:
    """The two deployment policies an execution is checked against.

    Both are enforced *before* a row is written, so a refusal leaves nothing
    behind for the user to clean up — and neither is a prompt instruction: the
    model cannot talk its way past either, because the check lives in the
    service that both the REST API and the AI tools call.
    """

    def test_project_operations_are_refused_without_egress(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                # An isolated deployment: the runtime has no network, so no
                # package index is reachable.
                harness.service.allows_package_install = False
                with pytest.raises(PackageInstallNotPermittedError) as exc:
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.PROJECT,
                        source="add pandas",
                    )
                assert "outbound network" in str(exc.value)
                # Nothing was queued and no row was written.
                assert harness.queue.executions == []
                _executions, total = (
                    await harness.repos.executions.list_executions_for_workspace(
                        harness.workspace_id, limit=10, offset=0
                    )
                )
                assert total == 0
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_scripts_still_run_without_egress(self, tmp_path: Path) -> None:
        """The policy blocks installation, not execution — that is the whole
        point of separating the two forms of network access."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                harness.service.allows_package_install = False
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print('offline but fine')",
                )
                assert execution.status is ExecutionStatus.QUEUED
                assert len(harness.queue.executions) == 1
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_full_workspace_blocks_submission(self, tmp_path: Path) -> None:
        """The capacity gate is consulted before the row is created."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:

                class FullWorkspace:
                    async def ensure_capacity(self, workspace_id: str, user_id: int) -> None:
                        raise WorkspaceQuotaExceededError(
                            "This workspace has used its whole disk allowance.",
                            used_bytes=10,
                            limit_bytes=5,
                        )

                harness.service.capacity = FullWorkspace()
                with pytest.raises(WorkspaceQuotaExceededError):
                    await harness.service.submit(
                        workspace_id=harness.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                    )
                assert harness.queue.executions == []
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_missing_capacity_gate_means_no_gate_not_a_bypass(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                assert harness.service.capacity is None
                execution = await harness.service.submit(
                    workspace_id=harness.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
                assert execution.status is ExecutionStatus.QUEUED
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())
