"""Workspace tools for MCP, as a collaborator of ``MCPToolBox``.

WHY separate from ``mcp_toolbox.py``: that module is the *document* tool surface
— files, folders, conversions, the folder-confinement gate. Workspaces are a
different resource with a different authorization story (a workspace is not in
the Drive, so there is no folder subtree to confine to), and putting them in one
file would make it harder to see which gate applies to which tool.

What is deliberately identical to the document tools:

* **Fail closed.** The scope is checked first, and the audit record is written
  only on success.
* **The same application services.** These methods call ``WorkspaceService`` and
  ``ExecutionService`` — the exact objects the REST API and the Transform AI use
  — so an agent can never reach a capability the web UI cannot, and cannot
  bypass a rule (ownership, the `uv` allow-list, the disk budget, the egress
  policy) that those services enforce.
* **Bounded output.** A listing, a file read and an execution's captured output
  all have ceilings, because an MCP response is a tool result an agent will put
  straight into its own context window.

One difference matters and is stated in the instructions an agent receives:
running code is *asynchronous*. ``run_code`` returns an execution id and often a
``running`` status, and the agent polls ``get_execution``. Holding the MCP
request open through a dependency installation would be the wrong shape for a
transport that is not designed for long-lived calls.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    WorkspaceNotFoundError,
    WorkspaceQuotaExceededError,
)
from src.application.services.workspaces.execution_service import ExecutionNotFoundError
from src.application.services.mcp.toolbox import MCPToolContext
from src.domain.security.value_object.agent_scope import AgentScope
from src.domain.workspaces.value_object.execution_mode import ExecutionMode

#: Ceilings applied to everything this surface returns.
_MAX_WORKSPACES = 50
_MAX_FILE_ENTRIES = 100
_MAX_FILE_BYTES = 64 * 1024
_MAX_WRITE_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_CHARS = 4_000


@runtime_checkable
class MCPWorkspacePort(Protocol):
    """The workspace operations the agent tools may use, all ownership-checked.

    Narrower than the full service on purpose: ``delete_workspace`` is absent, so
    no agent-facing tool can destroy a user's files and environment. Deletion
    stays a decision a human makes in the app.
    """

    async def list_workspaces(
        self, user_id: int, *, limit: int, offset: int
    ) -> tuple[list[Any], int]: ...

    async def get_detail(self, workspace_id: str, user_id: int) -> Any: ...

    async def create_workspace(
        self, *, user_id: int, name: str, description: str = ""
    ) -> Any: ...

    async def list_workspace_files(
        self, workspace_id: str, user_id: int, *, path: str = "."
    ) -> list[Any]: ...

    async def read_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str
    ) -> bytes: ...

    async def write_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str, data: bytes
    ) -> None: ...

    async def disk_usage(self, workspace_id: str, user_id: int) -> dict[str, int]: ...

    async def save_environment(self, workspace_id: str, user_id: int) -> Any: ...


@runtime_checkable
class MCPExecutionPort(Protocol):
    async def submit(
        self,
        *,
        workspace_id: str,
        user_id: int,
        mode: ExecutionMode,
        source: str,
        timeout_seconds: int = ...,
    ) -> Any: ...

    async def get(self, execution_id: str, user_id: int) -> Any: ...

    async def list_for_workspace(
        self, *, workspace_id: str, user_id: int, limit: int, offset: int
    ) -> tuple[list[Any], int]: ...

    async def cancel(self, execution_id: str, user_id: int) -> Any: ...


def _bounded(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Truncate keeping the tail (the part that explains a failure)."""
    if len(text) <= limit:
        return text, False
    marker = "… [earlier output omitted]\n"
    keep = max(0, limit - len(marker))
    return marker + text[len(text) - keep :], True


def _denied(scope: AgentScope, tool: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"This tool needs the {scope.value} permission.",
        "required_scope": scope.value,
        "tool": tool,
    }


def failures_as_values(fn: Any) -> Any:
    """Turn the *expected* failures of a tool into ``{"ok": false, ...}`` values.

    Applied as a decorator to every public tool so the conversion cannot be
    half-applied: an earlier version guarded individual calls and then used the
    returned error dict as if it were a result, which the tests caught by
    tripping an ``AttributeError`` on the error payload. One wrapper over the
    whole body removes that class of mistake.

    Only the conditions an agent can actually cause are caught — an
    unknown/foreign id and a policy refusal. An unexpected failure (a database
    outage) is deliberately NOT caught: that is a genuine fault and should
    surface as one rather than as a plausible-looking ``ok: false``.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = await fn(self, *args, **kwargs)
        except WorkspaceNotFoundError:
            # One wording for "missing" and "not yours": an agent must not be
            # able to use error messages to enumerate other accounts' ids.
            return {"ok": False, "error": "That workspace was not found."}
        except ExecutionNotFoundError:
            return {"ok": False, "error": "That execution was not found."}
        except WorkspaceQuotaExceededError as exc:
            return {"ok": False, "error": str(exc), "quota_exceeded": True}
        except PackageInstallNotPermittedError as exc:
            return {"ok": False, "error": str(exc), "egress_restricted": True}
        return result

    return wrapper


class MCPWorkspaceTools:
    """Workspace and code-execution tools for an authorized agent."""

    def __init__(
        self,
        *,
        workspaces: MCPWorkspacePort,
        executions: MCPExecutionPort,
        default_timeout_seconds: int = 300,
    ) -> None:
        self._workspaces = workspaces
        self._executions = executions
        self._default_timeout = default_timeout_seconds

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    @failures_as_values
    async def list_workspaces(self, ctx: MCPToolContext, *, limit: int = 20) -> dict[str, Any]:
        """The caller's cloud workspaces."""
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "list_workspaces")
        bounded = max(1, min(int(limit or 20), _MAX_WORKSPACES))
        workspaces, total = await self._workspaces.list_workspaces(
            ctx.user_id, limit=bounded, offset=0
        )
        return {
            "ok": True,
            "total": total,
            "workspaces": [
                {
                    "workspace_id": item.workspace_id,
                    "name": item.name,
                    "description": item.description,
                    "python_version": item.runtime_config.python_version,
                    "state": str(item.state),
                    "execution_count": item.execution_count,
                }
                for item in workspaces
            ],
        }

    @failures_as_values
    async def get_workspace(self, ctx: MCPToolContext, workspace_id: str) -> dict[str, Any]:
        """One workspace: runtime state, toolchain, budget and recent runs."""
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "get_workspace")
        detail = await self._workspaces.get_detail(workspace_id, ctx.user_id)
        runtime = detail.runtime
        usage = await self._workspaces.disk_usage(workspace_id, ctx.user_id)
        executions, _total = await self._executions.list_for_workspace(
            workspace_id=workspace_id, user_id=ctx.user_id, limit=5, offset=0
        )
        return {
            "ok": True,
            "workspace_id": detail.workspace.workspace_id,
            "name": detail.workspace.name,
            "state": str(detail.workspace.state),
            "runtime_state": str(runtime.state) if runtime else "STOPPED",
            "runtime_failure_reason": runtime.failure_reason if runtime else None,
            "python_version": detail.workspace.runtime_config.python_version,
            "uv_version": detail.workspace.runtime_config.uv_version,
            "resource_profile": detail.workspace.runtime_config.resource_profile,
            "active_executions": detail.active_executions,
            "disk": usage,
            "recent_executions": [
                {
                    "execution_id": item.execution_id,
                    "mode": str(item.mode),
                    "status": str(item.status),
                    "exit_code": item.exit_code,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in executions
            ],
            "persistence_note": (
                "Files, pyproject.toml and uv.lock survive stopping and restarting. "
                "Installed packages survive a stop/start of the same runtime, or are "
                "restored from a saved environment snapshot. Running processes and "
                "in-memory variables are never preserved."
            ),
        }

    @failures_as_values
    async def create_workspace(
        self, ctx: MCPToolContext, name: str, description: str = ""
    ) -> dict[str, Any]:
        """Create a workspace. Does not start any compute."""
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "create_workspace")
        workspace = await self._workspaces.create_workspace(
            user_id=ctx.user_id, name=name, description=description
        )
        return {
            "ok": True,
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "runtime_state": "STOPPED",
            "note": (
                "The runtime starts automatically the first time code runs here."
            ),
        }

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    @failures_as_values
    async def list_workspace_files(
        self, ctx: MCPToolContext, workspace_id: str, path: str = "."
    ) -> dict[str, Any]:
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "list_workspace_files")
        entries = await self._workspaces.list_workspace_files(
            workspace_id, ctx.user_id, path=path
        )
        listed = entries[:_MAX_FILE_ENTRIES]
        return {
            "ok": True,
            "path": path,
            "count": len(entries),
            "truncated": len(entries) > len(listed),
            "entries": [
                {
                    "name": entry.name,
                    "path": entry.path,
                    "is_dir": entry.is_dir,
                    "size_bytes": entry.size_bytes,
                }
                for entry in listed
            ],
        }

    @failures_as_values
    async def read_workspace_file(
        self, ctx: MCPToolContext, workspace_id: str, path: str
    ) -> dict[str, Any]:
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "read_workspace_file")
        data = await self._workspaces.read_workspace_file(
            workspace_id, ctx.user_id, path=path
        )
        truncated = len(data) > _MAX_FILE_BYTES
        payload = data[:_MAX_FILE_BYTES]
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "ok": False,
                "error": f"{path} is not a text file.",
                "path": path,
                "size_bytes": len(data),
            }
        return {
            "ok": True,
            "path": path,
            "size_bytes": len(data),
            "truncated": truncated,
            "content": content,
        }

    @failures_as_values
    async def write_workspace_file(
        self, ctx: MCPToolContext, workspace_id: str, path: str, content: str
    ) -> dict[str, Any]:
        """Write a text file. The content is written verbatim."""
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "write_workspace_file")
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return {
                "ok": False,
                "error": (
                    f"Content is {len(encoded)} bytes, over the "
                    f"{_MAX_WRITE_BYTES}-byte limit for one write."
                ),
            }
        await self._workspaces.write_workspace_file(
            workspace_id, ctx.user_id, path=path, data=encoded
        )
        return {"ok": True, "path": path, "bytes_written": len(encoded)}

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @failures_as_values
    async def run_code(
        self,
        ctx: MCPToolContext,
        workspace_id: str,
        source: str,
        mode: str = "script",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Submit code and return immediately with an execution id.

        Asynchronous by design: an MCP call is not a place to hold a dependency
        installation. The status in the response is the *initial* one, and the
        agent polls ``get_execution`` for the result.
        """
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "run_code")
        try:
            execution_mode = ExecutionMode(str(mode).strip().upper())
        except ValueError:
            return {
                "ok": False,
                "error": "mode must be one of 'SCRIPT', 'COMMAND' or 'PROJECT'.",
            }
        timeout = max(1, min(int(timeout_seconds or self._default_timeout), 3600))
        execution = await self._executions.submit(
            workspace_id=workspace_id,
            user_id=ctx.user_id,
            mode=execution_mode,
            source=source,
            timeout_seconds=timeout,
        )
        return {
            "ok": True,
            "execution_id": execution.execution_id,
            "status": str(execution.status),
            "note": (
                "The run is queued or running. Call get_execution with this "
                "execution_id to retrieve its result; do not assume it succeeded."
            ),
        }

    @failures_as_values
    async def get_execution(self, ctx: MCPToolContext, execution_id: str) -> dict[str, Any]:
        """One execution: status, exit code and bounded output."""
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "get_execution")
        execution = await self._executions.get(execution_id, ctx.user_id)
        stdout, out_cut = _bounded(execution.stdout_excerpt or "")
        stderr, err_cut = _bounded(execution.stderr_excerpt or "")
        return {
            "ok": True,
            "execution_id": execution.execution_id,
            "workspace_id": execution.workspace_id,
            "mode": str(execution.mode),
            "status": str(execution.status),
            "exit_code": execution.exit_code,
            "duration_ms": execution.duration_ms,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": out_cut or err_cut,
            "error": execution.error,
            "artifacts": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "stored": item.object_key is not None,
                }
                for item in execution.artifacts
            ],
        }

    @failures_as_values
    async def cancel_execution(
        self, ctx: MCPToolContext, execution_id: str
    ) -> dict[str, Any]:
        """Stop a run that is still going.

        WHY this exists: ``run_code`` can start work that takes minutes — a
        dependency install, a long script — and until this tool existed an agent
        that had started one had no way to stop it. A user saying "stop that" was
        unanswerable, and a runaway run held CPU until its timeout.

        Needs ``workspaces.execute`` rather than ``workspaces.read``: cancelling
        changes what the platform is doing, so read-only access must not be able
        to do it.
        """
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "cancel_execution")
        execution = await self._executions.cancel(execution_id, ctx.user_id)
        settled = str(execution.status) in ("CANCELLED", "SUCCEEDED", "FAILED", "TIMED_OUT")
        payload: dict[str, Any] = {
            "ok": True,
            "execution_id": execution.execution_id,
            "status": str(execution.status),
            "exit_code": execution.exit_code,
        }
        if not settled:
            # A cancel is a request; the worker may still be tearing the run down.
            payload["note"] = (
                "The cancel was recorded but the run has not stopped yet. Call "
                "get_execution to confirm before telling the user it stopped."
            )
        return payload

    @failures_as_values
    async def save_workspace_environment(
        self, ctx: MCPToolContext, workspace_id: str
    ) -> dict[str, Any]:
        """Snapshot the workspace's installed packages so a restart keeps them.

        WHY this exists: an agent that runs ``uv add`` changes the environment,
        and without a snapshot those packages are lost when the runtime is
        replaced. The user's own app offers "Save environment"; an agent that can
        install but not save would silently leave work that looks durable and is
        not.

        Needs ``workspaces.execute`` — it changes what the workspace will contain
        on its next start. The snapshot is built in the background, so this
        returns a revision that starts PENDING; the note says to check back rather
        than implying it is ready.
        """
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "save_workspace_environment")
        revision = await self._workspaces.save_environment(workspace_id, ctx.user_id)
        status = str(getattr(revision, "status", "PENDING"))
        payload: dict[str, Any] = {
            "ok": True,
            "workspace_id": workspace_id,
            "revision_id": getattr(revision, "revision_id", None),
            "status": status,
        }
        if status in ("PENDING", "BUILDING"):
            payload["note"] = (
                "The snapshot is being built in the background. It is not restored until "
                "a later start, so do not tell the user their packages are saved yet."
            )
        return payload

    @failures_as_values
    async def list_artifacts(self, ctx: MCPToolContext, workspace_id: str) -> dict[str, Any]:
        """Artifacts produced by a workspace's recent executions.

        ``stored`` says whether the bytes were copied into durable storage, so an
        agent does not promise a download for a file that only ever existed inside
        a container that has since been replaced.
        """
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "list_artifacts")
        executions, _total = await self._executions.list_for_workspace(
            workspace_id=workspace_id, user_id=ctx.user_id, limit=20, offset=0
        )
        artifacts = [
            {
                "execution_id": execution.execution_id,
                "path": artifact.path,
                "size_bytes": artifact.size_bytes,
                "stored": artifact.object_key is not None,
            }
            for execution in executions
            for artifact in execution.artifacts
        ]
        return {"ok": True, "artifacts": artifacts}
