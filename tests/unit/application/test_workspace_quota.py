"""Disk-quota enforcement on the workspace service.

What is being protected, and from which direction:

* **The API cannot grow a workspace past its budget.** Every write is measured
  against the current usage before the bytes are placed, so the intended path
  into the volume is closed.
* **Code cannot grow it without limit either.** ``ensure_capacity`` is called
  before an execution is queued, so a workspace whose code filled it up stops
  accepting new work until files are deleted.
* **Recovery works.** Deleting files restores capacity — a quota that could only
  be exceeded, never come back from, would be a trap rather than a limit.
* **Unmeasurable is not full.** A stopped runtime reports no usage, so a stopped
  workspace is never permanently blocked.

The honest limit is asserted too: the quota is measured, not a filesystem quota,
so a single execution can overshoot before the next measurement.
"""

import asyncio
from pathlib import Path

import pytest

from src.application.exceptions.workspace_exceptions import WorkspaceQuotaExceededError
from tests.fakes.fake_runtime_provider import FakeRuntimeProvider
from tests.fixtures.workspace_repos import build_repos, dispose_repos
from tests.unit.application.test_workspace_service import Harness, USER

MB = 1024 * 1024


async def _started_workspace(harness: Harness) -> str:
    workspace = await harness.service.create_workspace(user_id=USER, name="ws")
    await harness.service.start_runtime(workspace.workspace_id, USER)
    return workspace.workspace_id


class TestWriteEnforcement:
    def test_a_write_within_the_budget_is_allowed(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                await harness.service.write_workspace_file(
                    workspace_id, USER, path="a.txt", data=b"x" * 1000
                )
                assert harness.provider.workspace_files[workspace_id]["a.txt"] == b"x" * 1000
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_write_that_would_exceed_the_budget_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                await harness.service.write_workspace_file(
                    workspace_id, USER, path="a.txt", data=b"x" * (MB - 10)
                )
                with pytest.raises(WorkspaceQuotaExceededError) as exc:
                    await harness.service.write_workspace_file(
                        workspace_id, USER, path="b.txt", data=b"y" * 100
                    )
                # The message carries the numbers, so the user knows how much to
                # delete rather than only that something is wrong.
                assert "out of disk space" in str(exc.value)
                assert exc.value.limit_bytes == MB
                assert exc.value.used_bytes >= MB - 10
                # Nothing partial was written.
                assert "b.txt" not in harness.provider.workspace_files[workspace_id]
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_deleting_files_restores_capacity(self, tmp_path: Path) -> None:
        """A quota that could only be exceeded would be a trap, not a limit."""

        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                await harness.service.write_workspace_file(
                    workspace_id, USER, path="big.bin", data=b"x" * (MB - 1)
                )
                with pytest.raises(WorkspaceQuotaExceededError):
                    await harness.service.write_workspace_file(
                        workspace_id, USER, path="more.txt", data=b"y" * 100
                    )

                await harness.service.delete_workspace_file(
                    workspace_id, USER, path="big.bin"
                )

                await harness.service.write_workspace_file(
                    workspace_id, USER, path="more.txt", data=b"y" * 100
                )
                assert harness.provider.workspace_files[workspace_id]["more.txt"] == b"y" * 100
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_disabled_quota_enforces_nothing(self, tmp_path: Path) -> None:
        """``0`` disables enforcement, for a deployment that enforces it in the
        filesystem instead (or a test)."""

        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=0)
            try:
                workspace_id = await _started_workspace(harness)
                await harness.service.write_workspace_file(
                    workspace_id, USER, path="huge.bin", data=b"x" * (3 * MB)
                )
                assert len(harness.provider.workspace_files[workspace_id]["huge.bin"]) == 3 * MB
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_the_check_is_per_workspace(self, tmp_path: Path) -> None:
        """One full workspace must not block another."""

        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                first = await harness.service.create_workspace(user_id=USER, name="one")
                second = await harness.service.create_workspace(user_id=USER, name="two")
                await harness.service.start_runtime(first.workspace_id, USER)
                await harness.service.start_runtime(second.workspace_id, USER)

                await harness.service.write_workspace_file(
                    first.workspace_id, USER, path="big.bin", data=b"x" * (MB - 1)
                )
                with pytest.raises(WorkspaceQuotaExceededError):
                    await harness.service.write_workspace_file(
                        first.workspace_id, USER, path="more.txt", data=b"y" * 100
                    )

                # The other workspace is unaffected.
                await harness.service.write_workspace_file(
                    second.workspace_id, USER, path="fine.txt", data=b"z" * 1000
                )
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())


class TestExecutionCapacity:
    def test_a_full_workspace_refuses_new_work(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                # Code (or a large single write) took it over the limit.
                harness.provider.seed_file(workspace_id, "output.bin", b"x" * (MB + 1))

                with pytest.raises(WorkspaceQuotaExceededError) as exc:
                    await harness.service.ensure_capacity(workspace_id, USER)
                assert "whole disk allowance" in str(exc.value)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_workspace_within_budget_passes_capacity(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                harness.provider.seed_file(workspace_id, "small.txt", b"x" * 10)
                await harness.service.ensure_capacity(workspace_id, USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_a_stopped_runtime_reports_an_unmeasured_zero(self, tmp_path: Path) -> None:
        """Never block a workspace whose usage cannot be measured."""

        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=1)
            try:
                workspace_id = await _started_workspace(harness)
                harness.provider.seed_file(workspace_id, "big.bin", b"x" * (2 * MB))
                await harness.service.stop_runtime(workspace_id, USER, force=True)
                # Not measured, so not "full": the safe direction.
                await harness.service.ensure_capacity(workspace_id, USER)
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())

    def test_usage_is_reported_for_the_ui(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = Harness(await build_repos(tmp_path), FakeRuntimeProvider(), quota_bytes=MB)
            try:
                workspace_id = await _started_workspace(harness)
                harness.provider.seed_file(workspace_id, "a.txt", b"x" * 512)
                usage = await harness.service.disk_usage(workspace_id, USER)
                assert usage["used_bytes"] == 512
                assert usage["limit_bytes"] == MB
                assert usage["remaining_bytes"] == MB - 512
            finally:
                await dispose_repos(harness.repos)

        asyncio.run(scenario())
