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

import base64
from typing import Any, Protocol, runtime_checkable

from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    RuntimeBusyError,
    RuntimeNotFoundError,
    RuntimeNotRunningError,
    SnapshotFailedError,
    WorkspaceLimitReachedError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
    WorkspaceQuotaExceededError,
)
from src.application.services.workspaces.execution_service import ExecutionNotFoundError
from src.application.services.mcp.toolbox import MCPToolBox, MCPToolContext
from src.domain.security.value_object.agent_scope import AgentScope
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.infrastructure.logging.audit import log_data_access, log_permission_denied

#: Ceilings applied to everything this surface returns.
_MAX_WORKSPACES = 50
_MAX_FILE_ENTRIES = 100
_MAX_FILE_BYTES = 64 * 1024
_MAX_WRITE_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_CHARS = 4_000
#: Hard ceiling on one read/write of a workspace file in bytes. The workspace
#: volume is a container filesystem, and a model-supplied file has no business
#: being gigabytes through a tool call.


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

    async def start_runtime(self, workspace_id: str, user_id: int) -> Any:
        """Ensure a runtime exists and is running. Idempotent."""
        ...

    async def stop_runtime(self, workspace_id: str, user_id: int) -> Any: ...

    async def latest_revision(self, workspace_id: str, user_id: int) -> Any | None:
        """The most recent environment snapshot, or ``None`` if never saved."""
        ...


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

    async def list_artifacts(self, *, workspace_id: str, user_id: int) -> list[Any]:
        """Recent artifacts, already bounded by the service.

        Exposed rather than re-flattened here so the MCP surface inherits the
        service's own ceilings (scan depth and total count). Re-implementing the
        loop in the tool is how an unbounded listing reaches an agent's context
        window, and how the two surfaces drift.
        """
        ...

    async def cancel(self, execution_id: str, user_id: int) -> Any: ...

    async def get_artifact(self, execution_id: str, user_id: int, path: str) -> Any | None:
        """One stored artifact of an owned execution, or ``None``.

        Used by ``save_artifact_to_drive``. The service already refuses a foreign
        execution, so this cannot be used to reach another account's files.
        """
        ...


def _bounded(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Truncate keeping the tail (the part that explains a failure)."""
    if len(text) <= limit:
        return text, False
    marker = "… [earlier output omitted]\n"
    keep = max(0, limit - len(marker))
    return marker + text[len(text) - keep :], True


def _page_output(text: str, offset: int, limit: int) -> tuple[str, bool]:
    """One forward page of captured output, plus "is there more?".

    Forward paging (rather than the tail-only truncation this replaced) is what
    makes a long traceback readable from its beginning: an agent can walk the
    stream instead of being handed its last 4,000 characters with no indication
    that anything came before.
    """
    if offset >= len(text):
        return "", False
    end = min(len(text), offset + limit)
    return text[offset:end], end < len(text)


def _denied(scope: AgentScope, tool: str) -> dict[str, Any]:
    """The scope-refusal payload, **identical in shape** to the document tools.

    The two surfaces used to answer a missing permission differently (``error``
    only here, ``error`` + ``required_scope`` there), which made an agent's
    handling depend on which family of tool it had called. One envelope means one
    recovery path: ask the user to reconnect and allow the named scope.
    """
    return {
        "ok": False,
        "error": (
            f"This application was not granted the {scope.value!r} permission, "
            f"so {tool} is not available. Ask the user to reconnect and allow it."
        ),
        "required_scope": scope.value,
    }


def _authorization_denied(ctx: MCPToolContext, tool: str) -> dict[str, Any] | None:
    """Refuse every workspace tool when the grant behind the call is unusable."""
    if not ctx.authorization_error:
        return None
    log_permission_denied(
        resource="grant:unavailable",
        user_id=str(ctx.user_id),
        action="mcp.tool_call",
        tool=tool,
    )
    return {"ok": False, "error": ctx.authorization_error, "reconnect_required": True}


def _audit(ctx: MCPToolContext, tool: str, resource: str) -> None:
    """Record a successful workspace action in the security audit log.

    The document tools always did this; the workspace tools wrote nothing, so an
    agent running code or writing files left no audit trail at all.
    """
    log_data_access(
        user_id=str(ctx.user_id),
        action="mcp.tool_call",
        resource=resource,
        tool=tool,
        client_id=ctx.client_id,
        grant_id=ctx.grant_id,
    )


def failures_as_values(fn: Any) -> Any:
    """Turn the *expected* failures of a tool into ``{"ok": false, ...}`` values.

    Applied as a decorator to every public tool so the conversion cannot be
    half-applied: an earlier version guarded individual calls and then used the
    returned error dict as if it were a result, which the tests caught by
    tripping an ``AttributeError`` on the error payload. One wrapper over the
    whole body removes that class of mistake.

    Every condition an agent can cause or meet is caught, including the ones
    that describe *state* rather than a bad argument: a workspace with no runtime
    yet, a runtime that is stopped, busy, or archived. Those used to escape as
    raw exceptions, which the MCP transport renders as a bare "Error executing
    tool <name>" — true, unactionable, and indistinguishable from a server fault.
    An unexpected failure (a database outage) is deliberately NOT caught: that is
    a genuine fault and should surface as one rather than as a plausible-looking
    ``ok: false``.
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
        except WorkspaceLimitReachedError:
            return {
                "ok": False,
                "error": (
                    "The workspace limit for this account has been reached. "
                    "Delete a workspace in the app before creating another."
                ),
            }
        except WorkspaceNotUsableError:
            return {
                "ok": False,
                "error": "That workspace is archived or being deleted, so it cannot be used.",
            }
        except RuntimeNotFoundError:
            return {
                "ok": False,
                "error": (
                    "That workspace has no runtime yet. Run code in it once to start "
                    "one, then try this again."
                ),
            }
        except RuntimeNotRunningError:
            return {
                "ok": False,
                "error": (
                    "The workspace's runtime is not running. Start it by running code, "
                    "then try this again."
                ),
            }
        except RuntimeBusyError:
            return {
                "ok": False,
                "error": (
                    "The workspace's runtime is busy with another operation. "
                    "Wait for it to finish and try again."
                ),
            }
        except SnapshotFailedError:
            return {
                "ok": False,
                "error": (
                    "The environment snapshot could not be created. The workspace is "
                    "unchanged; try again."
                ),
            }
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
        documents: MCPToolBox | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._executions = executions
        self._default_timeout = default_timeout_seconds
        #: The document toolbox, used only as a *bridge* for the two tools that
        #: move bytes between the Drive and a workspace. Injected rather than
        #: re-implemented so the folder confinement, upload validation and quota
        #: checks are the exact ones every other file tool applies.
        self._documents = documents

    async def _ensure_runtime(self, workspace_id: str, user_id: int) -> None:
        """Start the workspace's runtime if it has none (or is stopped).

        The tool descriptions promised "the runtime is started automatically",
        while the service raised ``RuntimeNotFoundError`` and told the agent to
        "run code once" — a contradiction that made file tools unusable on a
        fresh workspace. ``start_runtime`` is idempotent, so this is safe to call
        on every file operation.
        """
        try:
            await self._workspaces.start_runtime(workspace_id, user_id)
        except RuntimeBusyError:
            # A concurrent start/stop is in flight. The operation that follows
            # will report its own, more specific state error if it still cannot
            # proceed.
            return

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    @failures_as_values
    async def list_workspaces(
        self, ctx: MCPToolContext, *, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """The caller's cloud workspaces."""
        auth = _authorization_denied(ctx, "list_workspaces")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "list_workspaces")
        bounded = max(1, min(limit or 20, _MAX_WORKSPACES))
        start = max(0, offset or 0)
        workspaces, total = await self._workspaces.list_workspaces(
            ctx.user_id, limit=bounded, offset=start
        )
        _audit(ctx, "list_workspaces", f"workspaces:{len(workspaces)}")
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
        auth = _authorization_denied(ctx, "get_workspace")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "get_workspace")
        detail = await self._workspaces.get_detail(workspace_id, ctx.user_id)
        runtime = detail.runtime
        usage = await self._workspaces.disk_usage(workspace_id, ctx.user_id)
        executions, _total = await self._executions.list_for_workspace(
            workspace_id=workspace_id, user_id=ctx.user_id, limit=5, offset=0
        )
        revision = await self._workspaces.latest_revision(workspace_id, ctx.user_id)
        _audit(ctx, "get_workspace", f"workspace:{workspace_id}")
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
            "environment_revision": (
                {
                    "revision_id": getattr(revision, "revision_id", None),
                    "status": str(getattr(revision, "status", "UNKNOWN")),
                    "created_at": (
                        revision.created_at.isoformat()
                        if getattr(revision, "created_at", None)
                        else None
                    ),
                }
                if revision is not None
                else None
            ),
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
        auth = _authorization_denied(ctx, "create_workspace")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "create_workspace")
        workspace = await self._workspaces.create_workspace(
            user_id=ctx.user_id, name=name, description=description
        )
        _audit(ctx, "create_workspace", f"workspace:{workspace.workspace_id}")
        return {
            "ok": True,
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "runtime_state": "STOPPED",
            "note": (
                "The runtime starts automatically the first time code runs here."
            ),
        }

    @failures_as_values
    async def start_workspace(
        self, ctx: MCPToolContext, workspace_id: str
    ) -> dict[str, Any]:
        """Start (or confirm) the workspace's runtime. Idempotent."""
        auth = _authorization_denied(ctx, "start_workspace")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "start_workspace")
        started = await self._workspaces.start_runtime(workspace_id, ctx.user_id)
        runtime = started.runtime
        _audit(ctx, "start_workspace", f"workspace:{workspace_id}")
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "runtime_state": str(runtime.state),
            "changed": bool(getattr(started, "changed", False)),
        }

    @failures_as_values
    async def stop_workspace(
        self, ctx: MCPToolContext, workspace_id: str
    ) -> dict[str, Any]:
        """Stop the workspace's runtime, preserving files and snapshots."""
        auth = _authorization_denied(ctx, "stop_workspace")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "stop_workspace")
        runtime = await self._workspaces.stop_runtime(workspace_id, ctx.user_id)
        _audit(ctx, "stop_workspace", f"workspace:{workspace_id}")
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "runtime_state": str(runtime.state),
            "note": (
                "Files, pyproject.toml and uv.lock survive. Running processes and "
                "in-memory variables do not."
            ),
        }

    @failures_as_values
    async def get_workspace_environment(
        self, ctx: MCPToolContext, workspace_id: str
    ) -> dict[str, Any]:
        """Status of the workspace's most recent environment snapshot."""
        auth = _authorization_denied(ctx, "get_workspace_environment")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "get_workspace_environment")
        revision = await self._workspaces.latest_revision(workspace_id, ctx.user_id)
        _audit(ctx, "get_workspace_environment", f"workspace:{workspace_id}")
        if revision is None:
            return {
                "ok": True,
                "workspace_id": workspace_id,
                "revision": None,
                "note": "No environment has been saved for this workspace yet.",
            }
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "revision": {
                "revision_id": getattr(revision, "revision_id", None),
                "status": str(getattr(revision, "status", "UNKNOWN")),
                "created_at": (
                    revision.created_at.isoformat()
                    if getattr(revision, "created_at", None)
                    else None
                ),
            },
        }

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    @failures_as_values
    async def list_workspace_files(
        self, ctx: MCPToolContext, workspace_id: str, path: str = "."
    ) -> dict[str, Any]:
        auth = _authorization_denied(ctx, "list_workspace_files")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "list_workspace_files")
        await self._ensure_runtime(workspace_id, ctx.user_id)
        entries = await self._workspaces.list_workspace_files(
            workspace_id, ctx.user_id, path=path
        )
        listed = entries[:_MAX_FILE_ENTRIES]
        _audit(ctx, "list_workspace_files", f"workspace:{workspace_id}")
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
        self,
        ctx: MCPToolContext,
        workspace_id: str,
        path: str,
        offset: int = 0,
        max_bytes: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Read a workspace file, as text (default) or base64.

        ``encoding="base64"`` is what makes a binary artifact (a chart PNG, a
        trained model) readable at all; the old text-only rule refused it.
        Paging is byte-based so a caller can walk a large file, and the text
        decoder only ever sees a whole slice — a split multi-byte character
        would otherwise render as a replacement glyph.
        """
        auth = _authorization_denied(ctx, "read_workspace_file")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "read_workspace_file")
        await self._ensure_runtime(workspace_id, ctx.user_id)
        data = await self._workspaces.read_workspace_file(
            workspace_id, ctx.user_id, path=path
        )
        total = len(data)
        start = max(0, offset or 0)
        cap = max(1, min(max_bytes or _MAX_FILE_BYTES, _MAX_FILE_BYTES))
        if start >= total and total > 0:
            return {
                "ok": False,
                "error": f"offset {start} is past the end of this file, which is {total} bytes.",
                "size_bytes": total,
            }
        window = data[start : start + cap]
        end = start + len(window)
        mode = (encoding or "utf-8").strip().lower()
        if mode in ("base64", "binary"):
            payload = {
                "ok": True,
                "path": path,
                "size_bytes": total,
                "offset": start,
                "bytes_returned": len(window),
                "has_more": end < total,
                "content_base64": base64.b64encode(window).decode("ascii"),
            }
            if end < total:
                payload["next_offset"] = end
            _audit(ctx, "read_workspace_file", f"workspace:{workspace_id}/{path}")
            return payload
        try:
            content = window.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "ok": False,
                "error": (
                    f"{path} is not UTF-8 text. Read it with encoding=\"base64\" instead."
                ),
                "path": path,
                "size_bytes": total,
            }
        _audit(ctx, "read_workspace_file", f"workspace:{workspace_id}/{path}")
        payload = {
            "ok": True,
            "path": path,
            "size_bytes": total,
            "offset": start,
            "bytes_returned": len(window),
            "has_more": end < total,
            "content": content,
        }
        if end < total:
            payload["next_offset"] = end
        return payload

    @failures_as_values
    async def write_workspace_file(
        self,
        ctx: MCPToolContext,
        workspace_id: str,
        path: str,
        content: str | None = None,
        content_base64: str | None = None,
    ) -> dict[str, Any]:
        """Write a file. The content is written verbatim.

        Exactly one of ``content`` (UTF-8 text) or ``content_base64`` (arbitrary
        bytes) is required. The base64 form is what makes "analyse my
        spreadsheet and save a chart" possible in the other direction: a model
        can move a binary it received without re-encoding it by hand.
        """
        auth = _authorization_denied(ctx, "write_workspace_file")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "write_workspace_file")
        if (content is None) == (content_base64 is None):
            return {
                "ok": False,
                "error": "Provide exactly one of content (text) or content_base64 (bytes).",
            }
        if content is not None:
            encoded = content.encode("utf-8")
        else:
            assert content_base64 is not None
            try:
                encoded = base64.b64decode(content_base64, validate=True)
            except (ValueError, TypeError):
                return {"ok": False, "error": "content_base64 is not valid base64 data."}
        if len(encoded) > _MAX_WRITE_BYTES:
            return {
                "ok": False,
                "error": (
                    f"Content is {len(encoded)} bytes, over the "
                    f"{_MAX_WRITE_BYTES}-byte limit for one write."
                ),
            }
        await self._ensure_runtime(workspace_id, ctx.user_id)
        await self._workspaces.write_workspace_file(
            workspace_id, ctx.user_id, path=path, data=encoded
        )
        _audit(ctx, "write_workspace_file", f"workspace:{workspace_id}/{path}")
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
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Submit code and return immediately with an execution id.

        Asynchronous by design: an MCP call is not a place to hold a dependency
        installation. The status in the response is the *initial* one, and the
        agent polls ``get_execution`` for the result.

        ``timeout_seconds`` defaults to the deployment setting rather than a
        hard-coded 300. A baked-in default silently overrode
        ``WORKSPACE_EXECUTION_TIMEOUT_SECONDS`` for every call that did not
        explicitly pass ``0``.
        """
        auth = _authorization_denied(ctx, "run_code")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "run_code")
        try:
            execution_mode = ExecutionMode(mode.strip().upper())
        except ValueError:
            return {
                "ok": False,
                "error": "mode must be one of 'SCRIPT', 'COMMAND' or 'PROJECT'.",
            }
        effective_timeout = (
            self._default_timeout
            if timeout_seconds is None
            else timeout_seconds
        )
        timeout = max(1, min(effective_timeout, 3600))
        execution = await self._executions.submit(
            workspace_id=workspace_id,
            user_id=ctx.user_id,
            mode=execution_mode,
            source=source,
            timeout_seconds=timeout,
        )
        _audit(ctx, "run_code", f"workspace:{workspace_id}->execution:{execution.execution_id}")
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
    async def get_execution(
        self,
        ctx: MCPToolContext,
        execution_id: str,
        offset: int = 0,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """One execution: status, exit code and pageable output.

        Output used to be truncated to the last 4,000 characters with no way to
        see the rest — so a traceback that began earlier was unrecoverable. Both
        streams are now paged by character with ``has_more`` / ``next_offset``
        (mirroring ``download_file`` and ``read_file_text``), and the default
        page is unchanged so nothing gets bigger by accident.
        """
        auth = _authorization_denied(ctx, "get_execution")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "get_execution")
        execution = await self._executions.get(execution_id, ctx.user_id)
        cap = max(200, min(max_chars or _MAX_OUTPUT_CHARS, 64_000))
        start = max(0, offset or 0)
        stdout, out_more = _page_output(execution.stdout_excerpt or "", start, cap)
        stderr, err_more = _page_output(execution.stderr_excerpt or "", start, cap)
        _audit(ctx, "get_execution", f"execution:{execution_id}")
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
            "output_offset": start,
            "output_has_more": out_more or err_more,
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
        auth = _authorization_denied(ctx, "cancel_execution")
        if auth:
            return auth
        execution = await self._executions.cancel(execution_id, ctx.user_id)
        _audit(ctx, "cancel_execution", f"execution:{execution_id}")
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
        auth = _authorization_denied(ctx, "save_workspace_environment")
        if auth:
            return auth
        revision = await self._workspaces.save_environment(workspace_id, ctx.user_id)
        _audit(ctx, "save_workspace_environment", f"workspace:{workspace_id}")
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
        auth = _authorization_denied(ctx, "list_artifacts")
        if auth:
            return auth
        # The service bounds both the scan (recent executions) and the total
        # count, so one pathological run cannot turn this into a token bomb.
        artifacts = await self._executions.list_artifacts(
            workspace_id=workspace_id, user_id=ctx.user_id
        )
        _audit(ctx, "list_artifacts", f"workspace:{workspace_id}")
        return {
            "ok": True,
            "artifacts": [
                {
                    "execution_id": ref.execution_id,
                    "path": ref.artifact.path,
                    "size_bytes": ref.artifact.size_bytes,
                    "stored": ref.artifact.object_key is not None,
                }
                for ref in artifacts
            ],
            "note": (
                "Only artifacts from the most recent runs are listed, and the list "
                "is capped; it is not necessarily exhaustive."
            ),
        }

    # ------------------------------------------------------------------
    # Bridges between the Drive and a workspace
    # ------------------------------------------------------------------

    @failures_as_values
    async def copy_file_to_workspace(
        self,
        ctx: MCPToolContext,
        file_id: str,
        workspace_id: str,
        path: str,
    ) -> dict[str, Any]:
        """Copy one of the user's Drive files into a workspace.

        This is the missing half of "analyse my spreadsheet": without it an agent
        could not get a Drive document in front of the Python it is allowed to
        run. Two permissions are required — ``documents.read`` to read the
        source and ``workspaces.execute`` to write into the workspace — and the
        *same* folder confinement as every document tool applies, because the
        read goes through ``MCPToolBox`` rather than a second implementation.
        """
        auth = _authorization_denied(ctx, "copy_file_to_workspace")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_EXECUTE):
            return _denied(AgentScope.WORKSPACES_EXECUTE, "copy_file_to_workspace")
        if self._documents is None:  # pragma: no cover - always wired
            return {"ok": False, "error": "Document access is not configured."}
        fetched = await self._documents.read_owned_bytes(ctx, file_id)
        if isinstance(fetched, dict):
            return fetched
        row, data = fetched
        await self._ensure_runtime(workspace_id, ctx.user_id)
        await self._workspaces.write_workspace_file(
            workspace_id, ctx.user_id, path=path, data=data
        )
        _audit(ctx, "copy_file_to_workspace", f"file:{file_id}->workspace:{workspace_id}")
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "path": path,
            "bytes_written": len(data),
            "source_file_id": row.id,
            "source_file_name": row.file_name,
        }

    @failures_as_values
    async def save_artifact_to_drive(
        self,
        ctx: MCPToolContext,
        execution_id: str,
        path: str,
        folder_id: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """Save a stored workspace artifact as a new file in the Drive.

        Needs ``workspaces.read`` (to see the execution's artifacts) and
        ``documents.write`` (to create the file). The bytes go through the normal
        upload pipeline — the same magic-byte validation, quota and folder rules
        as a browser upload — rather than a bespoke write, and the destination is
        confined to the grant's folder exactly like ``upload_file``.
        """
        auth = _authorization_denied(ctx, "save_artifact_to_drive")
        if auth:
            return auth
        if not ctx.allows(AgentScope.WORKSPACES_READ):
            return _denied(AgentScope.WORKSPACES_READ, "save_artifact_to_drive")
        if not ctx.allows(AgentScope.DOCUMENTS_WRITE):
            return _denied(AgentScope.DOCUMENTS_WRITE, "save_artifact_to_drive")
        if self._documents is None:  # pragma: no cover - always wired
            return {"ok": False, "error": "Document access is not configured."}
        artifact = await self._executions.get_artifact(execution_id, ctx.user_id, path)
        if artifact is None or artifact.object_key is None:
            return {
                "ok": False,
                "error": (
                    "No stored artifact with that path was found. Only artifacts copied "
                    "into durable storage can be saved to the Drive; check list_artifacts."
                ),
            }
        data = await self._documents.read_object_bytes(ctx, artifact.object_key)
        if isinstance(data, dict):
            return data
        name = file_name or path.rsplit("/", 1)[-1]
        saved = await self._documents.store_bytes_as_file(ctx, name, data, folder_id)
        if saved.get("ok"):
            _audit(
                ctx, "save_artifact_to_drive",
                f"execution:{execution_id}->file:{saved.get('file', {}).get('file_id', '')}",
            )
        return saved
