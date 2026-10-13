"""Workspace lifecycle behaviour, end to end through the service and real repos.

What these tests are really protecting:

* **Idempotent start.** The single most dangerous bug this feature can have is
  "start allocated a second runtime", so it is asserted directly — twice, and by
  counting the provider's ``create_runtime`` calls.
* **Continuity across a stop.** A stopped runtime restarts the *same* container,
  which is what makes the user's installed packages survive an ordinary
  stop/start.
* **Snapshot compatibility.** A snapshot built for another configuration, or on a
  replaced base image, must not be reused — otherwise the user is silently handed
  an environment they did not ask for.
* **Failure never destroys data.** A provider failure marks the runtime FAILED
  and leaves the workspace and its volume alone.
"""

import asyncio
from pathlib import Path

import pytest

from src.application.exceptions.runtime_provider_exceptions import (
    RuntimeProviderUnavailableError,
)
from src.application.exceptions.workspace_exceptions import (
    RuntimeBusyError,
    RuntimeNotFoundError,
    RuntimeNotRunningError,
    SnapshotFailedError,
    WorkspaceLimitReachedError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
)
from src.application.services.workspace_service import WorkspaceService
from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.runtime_config import RuntimeConfig
from src.domain.workspaces.value_object.runtime_state import RuntimeState
from tests.fakes.fake_runtime_provider import FakeRuntimeProvider
from tests.fakes.fake_workspace_queue import FakeWorkspaceQueue
from tests.fixtures.workspace_repos import WorkspaceRepos, build_repos, dispose_repos

USER = 7


class Harness:
    def __init__(
        self,
        repos: WorkspaceRepos,
        provider: FakeRuntimeProvider,
        *,
        quota_bytes: int = 0,
    ) -> None:
        self.repos = repos
        self.provider = provider
        self.queue = FakeWorkspaceQueue()
        self.service = WorkspaceService(
            workspace_repository=repos.workspaces,
            runtime_repository=repos.runtimes,
            revision_repository=repos.revisions,
            execution_repository=repos.executions,
            runtime_provider=provider,
            queue=self.queue,
            disk_quota_bytes=quota_bytes,
        )

    def provider_calls(self, method: str) -> int:
        return self.provider.calls.count(method)

    def image_for(self, runtime) -> str:
        """The image the provider actually created for a runtime.

        The ``provider_ref`` narrowing lives here so every assertion about which
        image was used reads the same way.
        """
        ref = runtime.provider_ref
        assert ref is not None, "the runtime has no provider handle"
        return self.provider.runtimes[ref].image


async def _harness(
    tmp_path: Path,
    provider: FakeRuntimeProvider | None = None,
    *,
    quota_bytes: int = 0,
) -> Harness:
    return Harness(
        await build_repos(tmp_path),
        provider or FakeRuntimeProvider(),
        quota_bytes=quota_bytes,
    )


def test_create_list_and_get(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path)
        try:
            workspace = await harness.service.create_workspace(
                user_id=USER, name="  Data   science "
            )
            assert workspace.name == "Data science"

            listed, total = await harness.service.list_workspaces(USER, limit=10, offset=0)
            assert total == 1
            assert [item.workspace_id for item in listed] == [workspace.workspace_id]

            fetched = await harness.service.get_workspace(workspace.workspace_id, USER)
            assert fetched.name == "Data science"
        finally:
            await dispose_repos(harness.repos)

    asyncio.run(scenario())


def test_another_users_workspace_is_reported_as_missing(tmp_path: Path) -> None:
    """One error for "gone" and "not yours" — no existence oracle over ids."""

    async def scenario() -> None:
        harness = await _harness(tmp_path)
        try:
            workspace = await harness.service.create_workspace(user_id=USER, name="Mine")
            with pytest.raises(WorkspaceNotFoundError):
                await harness.service.get_workspace(workspace.workspace_id, 999)
        finally:
            await dispose_repos(harness.repos)

    asyncio.run(scenario())


def test_the_workspace_limit_is_enforced(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path)
        try:
            harness.service.max_workspaces = 2
            await harness.service.create_workspace(user_id=USER, name="one")
            await harness.service.create_workspace(user_id=USER, name="two")
            with pytest.raises(WorkspaceLimitReachedError):
                await harness.service.create_workspace(user_id=USER, name="three")
        finally:
            await dispose_repos(harness.repos)

    asyncio.run(scenario())


class TestStartRuntime:
    def test_a_first_start_allocates_the_runtime_and_its_durable_state(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                started = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert started.changed is True
                assert started.runtime.state is RuntimeState.RUNNING
                assert started.runtime.provider_ref
                assert harness.provider_calls("ensure_workspace_state") == 1
                assert harness.provider_calls("create_runtime") == 1
                # The base image is addressed by name AND pinned revision.
                assert harness.image_for(started.runtime) == (
                    "transform-python-runtime:1.0.0-py3.14"
                )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_starting_twice_does_not_allocate_a_second_runtime(self, tmp_path: Path) -> None:
        """The failure the brief calls out, asserted by counting provider calls."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                first = await harness.service.start_runtime(workspace.workspace_id, USER)
                second = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert second.changed is False
                assert second.runtime.runtime_id == first.runtime.runtime_id
                assert second.runtime.provider_ref == first.runtime.provider_ref
                assert harness.provider_calls("create_runtime") == 1
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_stopped_runtime_is_restarted_not_recreated(self, tmp_path: Path) -> None:
        """Restarting the same container is what preserves the uv environment."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                started = await harness.service.start_runtime(workspace.workspace_id, USER)
                original_ref = started.runtime.provider_ref
                await harness.service.stop_runtime(workspace.workspace_id, USER)

                again = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert again.runtime.state is RuntimeState.RUNNING
                assert again.runtime.provider_ref == original_ref
                assert harness.provider_calls("create_runtime") == 1
                assert harness.provider_calls("start_runtime") == 1
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_provider_failure_marks_the_runtime_failed_and_keeps_the_workspace(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                harness.provider.fail_next["create_runtime"] = RuntimeProviderUnavailableError(
                    "no daemon"
                )

                with pytest.raises(RuntimeProviderUnavailableError):
                    await harness.service.start_runtime(workspace.workspace_id, USER)

                runtime = await harness.repos.runtimes.get_runtime_for_workspace(
                    workspace.workspace_id
                )
                assert runtime is not None
                assert runtime.state is RuntimeState.FAILED
                assert "no daemon" in (runtime.failure_reason or "")

                # The workspace, and therefore the user's files, are untouched.
                still_there = await harness.repos.workspaces.get_workspace(
                    workspace.workspace_id, USER
                )
                assert still_there is not None

                # And the next start is the recovery path.
                recovered = await harness.service.start_runtime(workspace.workspace_id, USER)
                assert recovered.runtime.state is RuntimeState.RUNNING
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_compatible_snapshot_is_used_instead_of_the_base_image(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                config = workspace.runtime_config
                revision = RuntimeImageRevision(
                    revision_id="rev-1",
                    workspace_id=workspace.workspace_id,
                    provider="fake",
                    config_fingerprint=config.fingerprint,
                    base_image_revision=config.base_image_revision,
                )
                revision.mark_building()
                revision.mark_ready(provider_snapshot_ref="fake-env:snap-1")
                await harness.repos.revisions.create_revision(revision)
                await harness.repos.revisions.update_revision(revision)

                started = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert harness.image_for(started.runtime) == "fake-env:snap-1"
                assert started.runtime.image_revision_id == "rev-1"
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_snapshot_from_a_replaced_base_image_is_not_reused(self, tmp_path: Path) -> None:
        """The stale-base-image case the brief singles out."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                revision = RuntimeImageRevision(
                    revision_id="rev-old",
                    workspace_id=workspace.workspace_id,
                    provider="fake",
                    config_fingerprint=workspace.runtime_config.fingerprint,
                    base_image_revision="0.9.0-py3.14",  # an obsolete image
                )
                revision.mark_building()
                revision.mark_ready(provider_snapshot_ref="fake-env:stale")
                await harness.repos.revisions.create_revision(revision)
                await harness.repos.revisions.update_revision(revision)

                started = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert harness.image_for(started.runtime) == (
                    "transform-python-runtime:1.0.0-py3.14"
                )
                assert started.runtime.image_revision_id is None
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_configuration_change_retires_the_container_built_from_the_old_one(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                first = await harness.service.start_runtime(workspace.workspace_id, USER)
                old_ref = first.runtime.provider_ref
                await harness.service.stop_runtime(workspace.workspace_id, USER)

                await harness.service.update_workspace(
                    workspace_id=workspace.workspace_id,
                    user_id=USER,
                    runtime_config=RuntimeConfig(base_image_revision="1.1.0-py3.14"),
                )
                second = await harness.service.start_runtime(workspace.workspace_id, USER)

                assert second.runtime.provider_ref != old_ref
                assert harness.provider_calls("delete_runtime") == 1
                assert harness.image_for(second.runtime) == (
                    "transform-python-runtime:1.1.0-py3.14"
                )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_an_archived_workspace_refuses_to_start(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.archive_workspace(workspace.workspace_id, USER)
                with pytest.raises(WorkspaceNotUsableError):
                    await harness.service.start_runtime(workspace.workspace_id, USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestStopRuntime:
    def test_stopping_a_workspace_with_no_runtime_is_a_not_found(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                with pytest.raises(RuntimeNotFoundError):
                    await harness.service.stop_runtime(workspace.workspace_id, USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_stop_is_refused_while_work_is_in_flight(self, tmp_path: Path) -> None:
        """Better to say so than to silently kill a run the user can still see."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)
                await harness.repos.executions.create_execution(
                    Execution(
                        execution_id="ex-1",
                        workspace_id=workspace.workspace_id,
                        user_id=USER,
                        mode=ExecutionMode.SCRIPT,
                        source="print(1)",
                    )
                )

                with pytest.raises(RuntimeBusyError):
                    await harness.service.stop_runtime(workspace.workspace_id, USER)

                # ...and a forced stop goes through anyway.
                stopped = await harness.service.stop_runtime(
                    workspace.workspace_id, USER, force=True
                )
                assert stopped.state is RuntimeState.STOPPED
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_restart_runs_stop_then_start(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)
                restarted = await harness.service.restart_runtime(
                    workspace.workspace_id, USER
                )
                assert restarted.changed is True
                assert restarted.runtime.state is RuntimeState.RUNNING
                assert harness.provider_calls("stop_runtime") == 1
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestEnvironment:
    def test_saving_requires_a_running_runtime(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                with pytest.raises(RuntimeNotRunningError):
                    await harness.service.save_environment(workspace.workspace_id, USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_saving_queues_a_pending_revision(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)

                revision = await harness.service.save_environment(
                    workspace.workspace_id, USER
                )

                assert revision.status.value == "PENDING"
                assert [item.revision_id for item in harness.queue.snapshots] == [
                    revision.revision_id
                ]
                assert revision.config_fingerprint == workspace.runtime_config.fingerprint
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_publish_failure_does_not_leave_a_revision_that_looks_in_flight(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)
                harness.queue.fail_snapshots = RuntimeError("redis is down")

                with pytest.raises(SnapshotFailedError):
                    await harness.service.save_environment(workspace.workspace_id, USER)

                revisions = await harness.service.list_revisions(
                    workspace.workspace_id, USER
                )
                assert len(revisions) == 1
                assert revisions[0].status.value == "FAILED"
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_runtime_configuration_reports_whether_a_snapshot_would_be_reused(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                config = workspace.runtime_config

                before = await harness.service.runtime_configuration(
                    workspace.workspace_id, USER
                )
                assert before["reusable_revision_id"] is None

                revision = RuntimeImageRevision(
                    revision_id="rev-1",
                    workspace_id=workspace.workspace_id,
                    provider="fake",
                    config_fingerprint=config.fingerprint,
                    base_image_revision=config.base_image_revision,
                )
                revision.mark_building()
                revision.mark_ready(provider_snapshot_ref="fake-env:1")
                await harness.repos.revisions.create_revision(revision)
                await harness.repos.revisions.update_revision(revision)

                after = await harness.service.runtime_configuration(
                    workspace.workspace_id, USER
                )
                assert after["reusable_revision_id"] == "rev-1"
                assert after["profile"].memory_mb > 0
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_provider_description_reports_unavailability_instead_of_raising(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                harness.provider.fail_next["describe"] = RuntimeProviderUnavailableError("down")
                described = await harness.service.provider_description()
                assert described["available"] is False
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestConfigurationEdits:
    def test_the_configuration_cannot_change_under_a_serving_runtime(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)

                with pytest.raises(RuntimeBusyError):
                    await harness.service.update_workspace(
                        workspace_id=workspace.workspace_id,
                        user_id=USER,
                        runtime_config=RuntimeConfig(resource_profile="large"),
                    )

                stopped = await harness.service.stop_runtime(
                    workspace.workspace_id, USER, force=True
                )
                assert stopped.state is RuntimeState.STOPPED

                updated = await harness.service.update_workspace(
                    workspace_id=workspace.workspace_id,
                    user_id=USER,
                    runtime_config=RuntimeConfig(resource_profile="large"),
                )
                assert updated.runtime_config.resource_profile == "large"
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_renaming_does_not_require_a_stopped_runtime(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.service.create_workspace(user_id=USER, name="ws")
                await harness.service.start_runtime(workspace.workspace_id, USER)
                renamed = await harness.service.update_workspace(
                    workspace_id=workspace.workspace_id, user_id=USER, name="renamed"
                )
                assert renamed.name == "renamed"
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


def test_deleting_a_workspace_tears_down_every_resource(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await _harness(tmp_path)
        try:
            workspace = await harness.service.create_workspace(user_id=USER, name="ws")
            started = await harness.service.start_runtime(workspace.workspace_id, USER)
            provider_ref = started.runtime.provider_ref
            assert provider_ref is not None
            harness.provider.seed_file(workspace.workspace_id, "main.py", b"print(1)")
            await harness.repos.executions.create_execution(
                Execution(
                    execution_id="ex-1",
                    workspace_id=workspace.workspace_id,
                    user_id=USER,
                    mode=ExecutionMode.SCRIPT,
                    source="print(1)",
                )
            )

            await harness.service.delete_workspace(workspace.workspace_id, USER)

            assert provider_ref not in harness.provider.runtimes
            assert workspace.workspace_id not in harness.provider.volumes
            assert (
                await harness.repos.workspaces.get_workspace(workspace.workspace_id, USER)
                is None
            )
            assert (
                await harness.repos.runtimes.get_runtime_for_workspace(
                    workspace.workspace_id
                )
                is None
            )
            executions, total = await harness.repos.executions.list_executions_for_workspace(
                workspace.workspace_id, limit=10, offset=0
            )
            assert (executions, total) == ([], 0)
        finally:
            await dispose_repos(harness.repos)

    asyncio.run(scenario())
