"""MCP workspace tools: scope enforcement, arguments, and honest results.

The properties under test are the ones an external agent's behaviour depends on:

* **Scope is enforced in the tool**, not described in a prompt: a grant without
  ``workspaces.execute`` cannot write a file or run code, and the refusal names
  the missing scope.
* **An unknown or foreign id is one answer.** The refusal for "not yours" is
  identical to "does not exist", so an agent cannot enumerate other accounts.
* **``run_code`` never claims completion.** It returns an execution id and a
  queued/running status; the result comes from ``get_execution``. An agent that
  trusted a "success" here would report work that never finished.
* **Artifacts distinguish stored from merely recorded**, so an agent does not
  promise a download that cannot exist.

The workspace and execution services are recording fakes: these tests are about
the tool contract, and the services themselves have their own suites including
real-SQLite tests.
"""

from pathlib import Path
from typing import Any

import pytest

from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    WorkspaceNotFoundError,
    WorkspaceQuotaExceededError,
)
from src.application.services.execution_service import ExecutionNotFoundError
from src.application.services.mcp_toolbox import MCPToolContext
from src.application.services.mcp_workspace_tools import MCPWorkspaceTools
from src.domain.security.value_object.agent_scope import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    SCOPE_DESCRIPTIONS,
    AgentScope,
    normalize_scopes,
)
from src.domain.workspaces.entities.execution import Execution, ExecutionArtifact
from src.domain.workspaces.entities.workspace import Workspace
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus

USER = 42


def _ctx(*scopes: AgentScope) -> MCPToolContext:
    return MCPToolContext(user_id=USER, scopes=tuple(scopes))


def _workspace(workspace_id: str = "ws-1") -> Workspace:
    return Workspace(workspace_id=workspace_id, user_id=USER, name="Analytics")


class _Detail:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.runtime = None
        self.active_executions = 0


class FakeWorkspaces:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.written: dict[str, bytes] = {}
        self.raise_on_detail: Exception | None = None

    async def list_workspaces(self, user_id: int, *, limit: int, offset: int):
        return [_workspace()], 1

    async def get_detail(self, workspace_id: str, user_id: int) -> Any:
        if self.raise_on_detail is not None:
            raise self.raise_on_detail
        return _Detail(_workspace(workspace_id))

    async def create_workspace(self, *, user_id: int, name: str, description: str = ""):
        self.created.append((name, description))
        return Workspace(workspace_id="ws-new", user_id=user_id, name=name)

    async def list_workspace_files(self, workspace_id: str, user_id: int, *, path: str = "."):
        class Entry:
            name = "main.py"
            path = "main.py"
            is_dir = False
            size_bytes = 12

        return [Entry()]

    async def read_workspace_file(self, workspace_id: str, user_id: int, *, path: str) -> bytes:
        return b"print(42)\n"

    async def write_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str, data: bytes
    ) -> None:
        self.written[path] = data

    async def disk_usage(self, workspace_id: str, user_id: int) -> dict[str, int]:
        return {"used_bytes": 10, "limit_bytes": 100, "remaining_bytes": 90}


class FakeExecutions:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._failure = failure

    async def submit(self, *, workspace_id, user_id, mode, source, timeout_seconds=300):
        if self._failure is not None:
            raise self._failure
        self.submitted.append(
            {"workspace_id": workspace_id, "mode": mode, "source": source}
        )
        return Execution(
            execution_id="ex-1",
            workspace_id=workspace_id,
            user_id=user_id,
            mode=mode,
            source=source,
        )

    async def get(self, execution_id: str, user_id: int) -> Any:
        if execution_id == "missing":
            raise ExecutionNotFoundError(execution_id)
        execution = Execution(
            execution_id=execution_id,
            workspace_id="ws-1",
            user_id=user_id,
            mode=ExecutionMode.SCRIPT,
            source="print(42)",
        )
        execution.start(runtime_id="rt-1")
        execution.succeed(
            exit_code=0,
            stdout="42\n",
            duration_ms=12,
            artifacts=[
                ExecutionArtifact(path="outputs/report.csv", size_bytes=8, object_key="k"),
                ExecutionArtifact(path="outputs/notes.txt", size_bytes=3),
            ],
        )
        return execution

    async def list_for_workspace(self, *, workspace_id, user_id, limit, offset):
        return [await self.get("ex-1", user_id)], 1


def _tools(
    workspaces: FakeWorkspaces | None = None, executions: FakeExecutions | None = None
) -> MCPWorkspaceTools:
    return MCPWorkspaceTools(
        workspaces=workspaces or FakeWorkspaces(),
        executions=executions or FakeExecutions(),
    )


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


class TestScopes:
    def test_the_new_scopes_are_known_and_described(self) -> None:
        assert AgentScope.WORKSPACES_READ in ALL_SCOPES
        assert AgentScope.WORKSPACES_EXECUTE in ALL_SCOPES
        # Every scope must have consent wording, or the consent screen would
        # ask for something it cannot explain.
        for scope in ALL_SCOPES:
            assert SCOPE_DESCRIPTIONS[scope]

    def test_workspace_scopes_are_not_granted_by_default(self) -> None:
        """Executing code on the platform is opt-in, never implied."""
        assert AgentScope.WORKSPACES_READ not in DEFAULT_SCOPES
        assert AgentScope.WORKSPACES_EXECUTE not in DEFAULT_SCOPES

    def test_an_unknown_workspace_scope_fails_closed(self) -> None:
        assert normalize_scopes(["workspaces.admin"]) == ()

    @pytest.mark.parametrize(
        "tool,scopes",
        [
            ("list_workspaces", ()),
            ("get_workspace", ()),
            ("create_workspace", ()),
            ("list_workspace_files", ()),
            ("read_workspace_file", ()),
            ("write_workspace_file", ()),
            ("run_code", ()),
            ("get_execution", ()),
            ("list_artifacts", ()),
        ],
    )
    def test_every_tool_refuses_without_its_scope(
        self, tool: str, scopes: tuple[AgentScope, ...]
    ) -> None:
        """An unscoped grant reaches nothing, and is told what it needs."""

        async def scenario() -> None:
            tools = _tools()
            ctx = _ctx(*scopes)
            if tool == "list_workspaces":
                result = await tools.list_workspaces(ctx)
            elif tool == "get_workspace":
                result = await tools.get_workspace(ctx, "ws-1")
            elif tool == "create_workspace":
                result = await tools.create_workspace(ctx, "n")
            elif tool == "list_workspace_files":
                result = await tools.list_workspace_files(ctx, "ws-1")
            elif tool == "read_workspace_file":
                result = await tools.read_workspace_file(ctx, "ws-1", "main.py")
            elif tool == "write_workspace_file":
                result = await tools.write_workspace_file(ctx, "ws-1", "a.py", "x")
            elif tool == "run_code":
                result = await tools.run_code(ctx, "ws-1", "print(1)")
            elif tool == "get_execution":
                result = await tools.get_execution(ctx, "ex-1")
            else:
                result = await tools.list_artifacts(ctx, "ws-1")

            assert result["ok"] is False
            assert result["required_scope"].startswith("workspaces.")
            assert result["tool"] == tool

        import asyncio

        asyncio.run(scenario())

    def test_read_scope_does_not_imply_execute(self) -> None:
        """The separation that matters: reading about a workspace is not running
        code in it."""

        async def scenario() -> None:
            tools = _tools()
            read_only = _ctx(AgentScope.WORKSPACES_READ)

            assert (await tools.list_workspaces(read_only))["ok"] is True
            assert (await tools.get_execution(read_only, "ex-1"))["ok"] is True

            denied = await tools.run_code(read_only, "ws-1", "print(1)")
            assert denied["ok"] is False
            assert denied["required_scope"] == "workspaces.execute"

            denied_write = await tools.write_workspace_file(read_only, "ws-1", "a.py", "x")
            assert denied_write["ok"] is False

            denied_create = await tools.create_workspace(read_only, "n")
            assert denied_create["ok"] is False

        import asyncio

        asyncio.run(scenario())

    def test_execute_scope_also_permits_reading(self) -> None:
        """A grant strong enough to run code does not need a second scope to see
        what it produced."""

        async def scenario() -> None:
            tools = _tools()
            ctx = _ctx(AgentScope.WORKSPACES_READ, AgentScope.WORKSPACES_EXECUTE)
            assert (await tools.list_workspaces(ctx))["ok"] is True
            assert (await tools.run_code(ctx, "ws-1", "print(1)"))["ok"] is True

        import asyncio

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Authorization of ids
# ---------------------------------------------------------------------------


class TestUnknownIds:
    def test_a_foreign_workspace_gets_one_answer(self) -> None:
        """Identical to "does not exist", so ids cannot be enumerated."""

        async def scenario() -> None:
            workspaces = FakeWorkspaces()
            workspaces.raise_on_detail = WorkspaceNotFoundError("ws-other")
            tools = _tools(workspaces)
            result = await tools.get_workspace(
                _ctx(AgentScope.WORKSPACES_READ), "ws-other"
            )
            assert result["ok"] is False
            assert result["error"] == "That workspace was not found."

        import asyncio

        asyncio.run(scenario())

    def test_an_unknown_execution_is_reported_as_a_value(self) -> None:
        async def scenario() -> None:
            result = await _tools().get_execution(
                _ctx(AgentScope.WORKSPACES_READ), "missing"
            )
            assert result["ok"] is False
            assert result["error"] == "That execution was not found."

        import asyncio

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestRunCode:
    def test_it_returns_an_id_and_never_claims_completion(self) -> None:
        async def scenario() -> None:
            executions = FakeExecutions()
            tools = _tools(executions=executions)
            result = await tools.run_code(
                _ctx(AgentScope.WORKSPACES_EXECUTE), "ws-1", "print(1)"
            )
            assert result["ok"] is True
            assert result["execution_id"] == "ex-1"
            # The initial status is QUEUED; the tool must say so rather than
            # implying the work is done.
            assert result["status"] == "QUEUED"
            assert "do not assume" in result["note"]
            assert executions.submitted[0]["mode"] is ExecutionMode.SCRIPT

        import asyncio

        asyncio.run(scenario())

    def test_an_invalid_mode_is_rejected_before_submitting(self) -> None:
        async def scenario() -> None:
            executions = FakeExecutions()
            tools = _tools(executions=executions)
            result = await tools.run_code(
                _ctx(AgentScope.WORKSPACES_EXECUTE), "ws-1", "ls", mode="SHELL"
            )
            assert result["ok"] is False
            assert "mode must be" in result["error"]
            assert executions.submitted == []

        import asyncio

        asyncio.run(scenario())

    def test_project_mode_is_reported_when_the_policy_forbids_it(self) -> None:
        """An isolated deployment answers with the policy, not a resolver error."""

        async def scenario() -> None:
            executions = FakeExecutions(
                failure=PackageInstallNotPermittedError("no outbound network access")
            )
            tools = _tools(executions=executions)
            result = await tools.run_code(
                _ctx(AgentScope.WORKSPACES_EXECUTE),
                "ws-1",
                "add pandas",
                mode="PROJECT",
            )
            assert result["ok"] is False
            assert result["egress_restricted"] is True
            assert "outbound network" in result["error"]

        import asyncio

        asyncio.run(scenario())

    def test_a_full_workspace_is_reported_as_a_quota_problem(self) -> None:
        async def scenario() -> None:
            executions = FakeExecutions(
                failure=WorkspaceQuotaExceededError(
                    "out of disk space", used_bytes=10, limit_bytes=5
                )
            )
            tools = _tools(executions=executions)
            result = await tools.run_code(
                _ctx(AgentScope.WORKSPACES_EXECUTE), "ws-1", "print(1)"
            )
            assert result["ok"] is False
            assert result["quota_exceeded"] is True

        import asyncio

        asyncio.run(scenario())

    def test_get_execution_reports_the_real_result_and_artifact_availability(self) -> None:
        async def scenario() -> None:
            result = await _tools().get_execution(
                _ctx(AgentScope.WORKSPACES_READ), "ex-1"
            )
            assert result["ok"] is True
            assert result["status"] == "SUCCEEDED"
            assert result["exit_code"] == 0
            assert result["stdout"] == "42\n"
            # `stored` is the difference between "you can fetch this" and "it
            # only ever existed inside a container".
            stored = {item["path"]: item["stored"] for item in result["artifacts"]}
            assert stored == {"outputs/report.csv": True, "outputs/notes.txt": False}

        import asyncio

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class TestFiles:
    def test_write_is_verbatim_and_recorded(self) -> None:
        async def scenario() -> None:
            workspaces = FakeWorkspaces()
            tools = _tools(workspaces)
            result = await tools.write_workspace_file(
                _ctx(AgentScope.WORKSPACES_EXECUTE), "ws-1", "src/a.py", "print('x')\n"
            )
            assert result["ok"] is True
            assert result["bytes_written"] == 11
            assert workspaces.written["src/a.py"] == b"print('x')\n"

        import asyncio

        asyncio.run(scenario())

    def test_an_oversized_write_is_refused_with_the_limit(self) -> None:
        async def scenario() -> None:
            result = await _tools().write_workspace_file(
                _ctx(AgentScope.WORKSPACES_EXECUTE),
                "ws-1",
                "big.txt",
                "x" * (2 * 1024 * 1024 + 1),
            )
            assert result["ok"] is False
            assert "limit" in result["error"]

        import asyncio

        asyncio.run(scenario())

    def test_reading_and_listing_report_bounded_content(self) -> None:
        async def scenario() -> None:
            tools = _tools()
            ctx = _ctx(AgentScope.WORKSPACES_READ)
            listing = await tools.list_workspace_files(ctx, "ws-1")
            assert listing["ok"] is True
            assert listing["entries"][0]["name"] == "main.py"

            read = await tools.read_workspace_file(ctx, "ws-1", "main.py")
            assert read["ok"] is True
            assert read["content"] == "print(42)\n"
            assert read["truncated"] is False

        import asyncio

        asyncio.run(scenario())

    def test_creating_a_workspace_requires_the_execute_scope(self) -> None:
        async def scenario() -> None:
            workspaces = FakeWorkspaces()
            tools = _tools(workspaces)
            result = await tools.create_workspace(
                _ctx(AgentScope.WORKSPACES_EXECUTE), "Notes", "for analysis"
            )
            assert result["ok"] is True
            assert workspaces.created == [("Notes", "for analysis")]
            # Creation starts no compute, and says so.
            assert result["runtime_state"] == "STOPPED"

        import asyncio

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Duplication guard
# ---------------------------------------------------------------------------


class TestSingleImplementation:
    def test_the_mcp_tools_expose_no_capability_the_service_does_not_have(self) -> None:
        """Every method the tools call must exist on the workspace service.

        This is what keeps "MCP cannot do anything the API could not" true by
        construction: the agent tools hold the workspace/execution services
        behind ports, and this asserts the ports stay a subset of them.
        """
        from src.application.services.workspace_service import WorkspaceService

        for name in (
            "list_workspaces",
            "get_detail",
            "create_workspace",
            "list_workspace_files",
            "read_workspace_file",
            "write_workspace_file",
            "disk_usage",
        ):
            assert hasattr(WorkspaceService, name), name
        # And the capability an agent must NOT have is absent from the port the
        # tools depend on, not merely unused.
        from src.application.services.mcp_workspace_tools import MCPWorkspacePort

        assert not hasattr(MCPWorkspacePort, "delete_workspace")


def test_no_workspace_path_is_reachable_without_a_scope(tmp_path: Path) -> None:
    """Belt and braces: the port has no method that ignores its context.

    Every tool method takes ``ctx`` first, which is what makes the scope check
    impossible to forget — a method without it could not be a tool at all.
    """

    import inspect

    tools = MCPWorkspaceTools.__dict__
    public = [
        name
        for name, value in tools.items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    ]
    assert public
    for name in public:
        signature = inspect.signature(getattr(MCPWorkspaceTools, name))
        assert "ctx" in signature.parameters, name
