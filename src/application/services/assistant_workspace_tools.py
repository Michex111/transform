"""Workspace tools for the assistant: the runtime, reachable by the model.

WHY a separate collaborator rather than more methods on ``AssistantToolBox``:
the file/conversion tools answer questions about documents the user already
has; these tools *run arbitrary code* on the user's behalf. Keeping them in
their own object makes the one surface with that power auditable in a single
place, and it keeps the 13 existing tools — and the tests that pin them —
untouched.

Four rules hold for every tool here, and each is load-bearing:

* **Ownership is re-validated on every call.** ``WorkspaceService`` is the same
  object the REST router uses, so a workspace id the model recalled from an
  earlier message is checked as rigorously as one in a URL. A workspace id is
  *context*, never an authorization token.
* **Long work is submitted, never awaited.** ``run_python`` writes a durable
  execution row and enqueues it; it waits only a short bounded interval for a
  trivial script to settle. Anything longer comes back as ``status: "running"``
  with an id, and the model inspects it with ``get_execution``. Holding the HTTP
  stream open for a dependency install is exactly what the durable records exist
  to avoid.
* **Output is bounded and redacted here.** The model receives at most a few
  thousand characters of stdout/stderr, and anything shaped like a credential is
  removed before it reaches the transcript — a tool result is persisted in the
  conversation and replayed on every later turn, so a leaked secret would be
  permanent and would follow the user across devices.
* **Failures are values.** A missing workspace, a rejected `uv` operation or a
  failed execution returns a sentence the model can act on, never an exception
  that aborts the turn.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from src.application.dtos.assistant_dto import Artifact
from src.application.ports.llm_port import LlmToolSpec
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus

#: Characters of an execution's stdout/stderr handed to the model. Small on
#: purpose: a tool result is replayed in the prompt on every subsequent turn, so
#: a 100 kB traceback would be a standing cost on the whole conversation.
_MAX_OUTPUT_CHARS = 4_000
#: Files returned by one listing.
_MAX_FILE_ENTRIES = 60
#: Bytes returned by one file read.
_MAX_READ_BYTES = 64 * 1024
#: Bytes accepted by one file write. Mirrors the API's own write ceiling so the
#: tool cannot be a way around it.
_MAX_WRITE_BYTES = 2 * 1024 * 1024
#: Workspaces returned by one listing.
_MAX_WORKSPACES = 50
#: Executions summarised by ``get_workspace``.
_MAX_RECENT_EXECUTIONS = 5

#: How long ``run_python`` waits for a submitted execution before answering
#: "still running". Short by design: this is a *latency trick* so a one-line
#: script answers inside the same turn, not a timeout for real work. A dependency
#: install or a real program must return to the model as a pending execution.
_FAST_RESULT_WAIT_SECONDS = 2.5
_FAST_RESULT_POLL_SECONDS = 0.25

#: Workspaces given a clickable chip by ``list_workspaces``. Bounded for the same
#: reason the file tools bound their artifacts: a long list must not render
#: hundreds of chips.
_MAX_ARTIFACT_WORKSPACES = 5

#: Patterns scrubbed from captured output before it is shown to the model.
#:
#: This is defence in depth, not the primary control: the runtime never receives
#: platform credentials, so there should be nothing to find. It exists because
#: the *user's own* script may print a token it read from a file, and that text
#: would otherwise be persisted in the conversation and replayed forever.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `Authorization: Bearer …`, `api_key=…`, `token: …`, `password=…`
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|authorization)"
        r"\b\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    # Provider key shapes worth catching even when unlabelled.
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    """Replace credential-shaped substrings with ``[redacted]``.

    Applied to every captured stream before it leaves this module. Both the
    model's copy and the artifact the user can open come from the same string, so
    redacting once here covers the transcript, the API response and the stored
    message.
    """
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


def bound_text(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Truncate to ``limit`` characters, reporting whether anything was lost.

    Keeps the TAIL when the text is long, for the same reason the execution
    excerpt does: the part a reader needs from a failing program is the end (the
    traceback, the final error), and a head-only cut of a chatty run shows
    nothing but startup noise.
    """
    if len(text) <= limit:
        return text, False
    marker = "… [earlier output omitted]\n"
    keep = max(0, limit - len(marker))
    return marker + text[len(text) - keep :], True


@runtime_checkable
class AssistantWorkspacePort(Protocol):
    """The workspace operations the tools may use, all ownership-checked.

    Declared as a narrow port so the powers granted to the model are readable in
    one place. Notably absent, and deliberately so: ``delete_workspace``,
    ``archive_workspace`` and ``delete_workspace_file``. Deleting a workspace
    destroys the user's files and has no confirmation step in the assistant, so
    it is not offered to a model at all.
    """

    async def require_workspace(self, workspace_id: str, user_id: int) -> Any: ...

    async def list_workspaces(
        self, user_id: int, *, limit: int, offset: int
    ) -> tuple[list[Any], int]: ...

    async def create_workspace(
        self, *, user_id: int, name: str, description: str = ""
    ) -> Any: ...

    async def get_detail(self, workspace_id: str, user_id: int) -> Any: ...

    async def runtime_configuration(self, workspace_id: str, user_id: int) -> dict[str, Any]: ...

    async def list_revisions(
        self, workspace_id: str, user_id: int, *, limit: int = 20
    ) -> list[Any]: ...

    async def start_runtime(self, workspace_id: str, user_id: int) -> Any: ...

    async def save_environment(self, workspace_id: str, user_id: int) -> Any: ...

    async def list_workspace_files(
        self, workspace_id: str, user_id: int, *, path: str = "."
    ) -> list[Any]: ...

    async def read_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str
    ) -> bytes: ...

    async def write_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str, data: bytes
    ) -> None: ...


@runtime_checkable
class AssistantExecutionPort(Protocol):
    """The execution operations the tools may use, all ownership-checked."""

    async def submit(
        self,
        *,
        workspace_id: str,
        user_id: int,
        mode: ExecutionMode,
        source: str,
        timeout_seconds: int = ...,
        idempotency_key: str | None = None,
    ) -> Any: ...

    async def get(self, execution_id: str, user_id: int) -> Any: ...

    async def cancel(self, execution_id: str, user_id: int) -> Any: ...

    async def list_for_workspace(
        self, *, workspace_id: str, user_id: int, limit: int, offset: int
    ) -> tuple[list[Any], int]: ...


@dataclass(frozen=True)
class WorkspaceTurnContext:
    """Which workspace this turn is about, if any.

    ``workspace_id`` is *requested* context: it is what the client asked to work
    on (or what a previous turn used). It is never trusted as authority — every
    tool re-reads the workspace through the service before doing anything.
    """

    workspace_id: str | None = None
    source: str = "none"
    """``"selected"`` (the client named it this turn) or ``"conversation"``
    (recalled from the transcript) or ``"none"``. Only used for the prompt line."""


def _tool_schema(
    properties: dict[str, Any], required: Sequence[str] = ()
) -> dict[str, Any]:
    """JSON schema for a tool's arguments.

    ``additionalProperties: false`` matches the existing tools: an invented
    argument would be silently ignored, which reads as the assistant not
    listening.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


#: The names this toolbox owns. Exported so the composing ``AssistantToolBox``
#: can route a call without importing each name individually and without
#: duplicating the list — a name that drifts out of sync here would silently
#: become an "Unknown tool" at runtime.
WORKSPACE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "list_workspaces",
        "get_workspace",
        "create_workspace",
        "list_workspace_files",
        "read_workspace_file",
        "write_workspace_file",
        "run_python",
        "get_execution",
        "cancel_execution",
        "save_workspace_environment",
    }
)

#: One-line UI phrasing per tool, shown while the call runs.
_TOOL_LABELS: dict[str, str] = {
    "list_workspaces": "Looking at your workspaces",
    "get_workspace": "Checking the workspace",
    "create_workspace": "Creating a workspace",
    "list_workspace_files": "Listing workspace files",
    "read_workspace_file": "Reading a workspace file",
    "write_workspace_file": "Writing into the workspace",
    "run_python": "Running Python in the workspace",
    "get_execution": "Checking the run",
    "cancel_execution": "Stopping the run",
    "save_workspace_environment": "Saving the environment",
}


class AssistantWorkspaceToolBox:
    """Executes the workspace tools the model may call."""

    def __init__(
        self,
        *,
        workspaces: AssistantWorkspacePort,
        executions: AssistantExecutionPort,
        default_timeout_seconds: int = 300,
        max_active_executions: int = 5,
    ) -> None:
        self._workspaces = workspaces
        self._executions = executions
        self._default_timeout = default_timeout_seconds
        self._max_active = max_active_executions

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def specs(self) -> list[LlmToolSpec]:
        """Every workspace tool, described for the model."""
        return [
            LlmToolSpec(
                name="list_workspaces",
                description=(
                    "List the user's cloud workspaces: persistent Python "
                    "environments that keep their files and installed packages. "
                    "Call this first when the user mentions 'my workspace' "
                    "without naming one, or when you need to know whether they "
                    "have any. Returns each workspace's id, name, runtime state "
                    "and Python version."
                ),
                parameters=_tool_schema(
                    {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_WORKSPACES,
                            "description": "Maximum workspaces to return (default 20).",
                        }
                    }
                ),
            ),
            LlmToolSpec(
                name="get_workspace",
                description=(
                    "Get one workspace's details: its runtime state and failure "
                    "reason if any, its Python and uv versions and resource "
                    "profile, whether a saved environment snapshot exists, and "
                    "its most recent executions. Use this before running code so "
                    "you know whether the runtime is already running."
                ),
                parameters=_tool_schema(
                    {"workspace_id": {"type": "string"}}, required=["workspace_id"]
                ),
            ),
            LlmToolSpec(
                name="create_workspace",
                description=(
                    "Create a new cloud workspace. This is cheap and reversible "
                    "and does NOT start any compute — the runtime starts the "
                    "first time code is run. Only call this when the user asks "
                    "for a new one or has none; never create one as a side effect "
                    "of an unrelated request."
                ),
                parameters=_tool_schema(
                    {
                        "name": {
                            "type": "string",
                            "description": "Short, human name for the workspace.",
                        },
                        "description": {
                            "type": ["string", "null"],
                            "description": "Optional note about what it is for.",
                        },
                    },
                    required=["name"],
                ),
            ),
            LlmToolSpec(
                name="list_workspace_files",
                description=(
                    "List the files and directories in a workspace. Paths are "
                    "relative to the workspace root. The workspace's runtime is "
                    "started automatically if it is not already running."
                ),
                parameters=_tool_schema(
                    {
                        "workspace_id": {"type": ["string", "null"]},
                        "path": {
                            "type": ["string", "null"],
                            "description": (
                                "Directory to list, relative to the workspace "
                                "root. Omit for the root."
                            ),
                        },
                    }
                ),
            ),
            LlmToolSpec(
                name="read_workspace_file",
                description=(
                    "Read a text file from a workspace. Binary files are refused "
                    "rather than returned as garbage. Large files are truncated. "
                    "The runtime is started automatically if needed."
                ),
                parameters=_tool_schema(
                    {
                        "workspace_id": {"type": ["string", "null"]},
                        "path": {
                            "type": "string",
                            "description": "File path relative to the workspace root.",
                        },
                    },
                    required=["path"],
                ),
            ),
            LlmToolSpec(
                name="write_workspace_file",
                description=(
                    "Write a text file into a workspace, creating parent "
                    "directories as needed (this is how you create a directory). "
                    "Writes the content EXACTLY as given — write real code, not a "
                    "description of code. The runtime is started automatically if "
                    "it is not already running."
                ),
                parameters=_tool_schema(
                    {
                        "workspace_id": {"type": ["string", "null"]},
                        "path": {
                            "type": "string",
                            "description": "File path relative to the workspace root.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The exact file content.",
                        },
                    },
                    required=["path", "content"],
                ),
            ),
            LlmToolSpec(
                name="run_python",
                description=(
                    "Run code in a workspace. mode='script' runs Python with uv, "
                    "so packages previously installed in the workspace are "
                    "importable. mode='command' runs a shell command line. "
                    "mode='project' performs ONE allowed uv operation: 'sync', "
                    "'lock', or 'add <package>' — use 'add' to install a "
                    "dependency. Returns the execution id, and either its result "
                    "or a 'running' status you can check with get_execution. "
                    "Never claim code succeeded until get_execution shows it "
                    "finished."
                ),
                parameters=_tool_schema(
                    {
                        "workspace_id": {"type": ["string", "null"]},
                        "mode": {
                            "type": ["string", "null"],
                            "enum": ["script", "command", "project", None],
                            "description": "Defaults to 'script'.",
                        },
                        "source": {
                            "type": "string",
                            "description": (
                                "The Python code, shell command, or uv operation "
                                "(e.g. 'add pandas') to run."
                            ),
                        },
                        "timeout_seconds": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "maximum": 3600,
                            "description": "Time limit; defaults to 300.",
                        },
                    },
                    required=["source"],
                ),
            ),
            LlmToolSpec(
                name="get_execution",
                description=(
                    "Check one execution: its status, exit code, duration, "
                    "stdout, stderr and any artifacts it produced. Call this "
                    "after run_python returns a 'running' status, and before "
                    "telling the user what a run produced."
                ),
                parameters=_tool_schema(
                    {"execution_id": {"type": "string"}}, required=["execution_id"]
                ),
            ),
            LlmToolSpec(
                name="cancel_execution",
                description=(
                    "Cancel a queued or running execution. Use only when the user "
                    "asks to stop it. Cancelling does not delete anything the run "
                    "already wrote."
                ),
                parameters=_tool_schema(
                    {"execution_id": {"type": "string"}}, required=["execution_id"]
                ),
            ),
            LlmToolSpec(
                name="save_workspace_environment",
                description=(
                    "Save a workspace's currently installed packages as a "
                    "reusable environment snapshot, so a later start restores "
                    "them without reinstalling. Requires a running runtime; the "
                    "snapshot is built in the background and appears as PENDING "
                    "then READY."
                ),
                parameters=_tool_schema(
                    {"workspace_id": {"type": ["string", "null"]}}
                ),
            ),
        ]

    @staticmethod
    def label_for(name: str) -> str:
        return _TOOL_LABELS.get(name, "Working in the workspace")

    @staticmethod
    def describe(name: str, result: dict[str, Any]) -> str:
        """One-line, user-facing summary derived from the actual result.

        Never written by the model: a model-authored summary of its own tool call
        is the thing that drifts from what really happened.
        """
        error = result.get("error")
        if isinstance(error, str):
            return error
        if name == "list_workspaces":
            count = result.get("count", 0)
            return f"Found {count} workspace(s)"
        if name == "get_workspace":
            state = result.get("runtime_state", "STOPPED")
            return f"Workspace is {str(state).lower()}"
        if name == "create_workspace":
            return f"Created the workspace {result.get('name', '')}".strip()
        if name == "list_workspace_files":
            return f"Found {result.get('count', 0)} item(s)"
        if name == "read_workspace_file":
            return f"Read {result.get('char_count', 0)} character(s)"
        if name == "write_workspace_file":
            return f"Wrote {result.get('path', 'the file')}"
        if name == "run_python":
            status = result.get("status")
            if status == "running":
                return "Submitted the run to the workspace"
            if status in ("SUCCEEDED", "finished"):
                return f"Ran it in {result.get('duration_ms', 0)} ms"
            if status == "TIMED_OUT":
                return "The run hit its time limit"
            if status == "FAILED":
                return "The run failed"
            return "Ran it"
        if name == "get_execution":
            return f"Status: {result.get('status', 'unknown')}"
        if name == "cancel_execution":
            return "Cancelled the run"
        if name == "save_workspace_environment":
            return "Queued an environment snapshot"
        return "Done"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: int,
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        """Run a workspace tool, returning a JSON-safe result.

        Mirrors ``AssistantToolBox.execute``: broad exception handling is
        deliberate, because every failure mode here (a database error, a provider
        outage, a bad path) has to reach the model as a sentence it can act on
        rather than aborting a turn the user is watching.
        """
        try:
            return await self._dispatch(
                name, arguments, user_id=user_id, artifacts=artifacts, context=context
            )
        except Exception as exc:  # noqa: BLE001 - a tool must never abort the loop
            return {"error": f"That workspace action could not be completed: {exc}"}

    async def _dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: int,
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        if name == "list_workspaces":
            return await self._list_workspaces(user_id, arguments, artifacts)
        if name == "get_workspace":
            return await self._get_workspace(user_id, arguments, artifacts, context)
        if name == "create_workspace":
            return await self._create_workspace(user_id, arguments, artifacts)
        if name == "list_workspace_files":
            return await self._list_files(user_id, arguments, artifacts, context)
        if name == "read_workspace_file":
            return await self._read_file(user_id, arguments, artifacts, context)
        if name == "write_workspace_file":
            return await self._write_file(user_id, arguments, artifacts, context)
        if name == "run_python":
            return await self._run_python(user_id, arguments, artifacts, context)
        if name == "get_execution":
            return await self._get_execution(user_id, arguments)
        if name == "cancel_execution":
            return await self._cancel_execution(user_id, arguments)
        if name == "save_workspace_environment":
            return await self._save_environment(user_id, arguments, artifacts, context)
        return {"error": f"Unknown workspace tool {name!r}."}

    # ------------------------------------------------------------------
    # Workspace resolution
    # ------------------------------------------------------------------

    async def _require_workspace_id(
        self, user_id: int, arguments: dict[str, Any], context: WorkspaceTurnContext
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Resolve the workspace to act on, or return an error payload.

        Resolution order is explicit argument → this turn's selected workspace.
        When neither exists the model is told to ask, rather than being handed
        an arbitrary workspace: picking one silently is how code runs somewhere
        the user did not expect.

        The id is only *resolved* here. Authorisation happens in the service
        call that follows, on every single call.
        """
        requested = self._as_str(arguments, "workspace_id") or context.workspace_id
        if not requested:
            workspaces, total = await self._workspaces.list_workspaces(
                user_id, limit=_MAX_WORKSPACES, offset=0
            )
            if total == 0:
                return None, {
                    "error": (
                        "You have no cloud workspaces yet. Ask the user whether "
                        "you should create one (create_workspace), or which "
                        "existing workspace to use."
                    )
                }
            names = ", ".join(f"{item.name} ({item.workspace_id})" for item in workspaces[:5])
            return None, {
                "error": (
                    "No workspace is selected for this conversation. Ask the user "
                    f"which one to use; available: {names}."
                ),
                "available": [
                    {"workspace_id": item.workspace_id, "name": item.name}
                    for item in workspaces[:5]
                ],
            }
        return requested, None

    async def _workspace_artifact(self, user_id: int, workspace_id: str) -> Artifact | None:
        workspace = await self._workspaces.require_workspace(workspace_id, user_id)
        return Artifact(
            type="workspace",
            id=workspace.workspace_id,
            name=workspace.name,
            meta={"state": str(workspace.state)},
        )

    async def _ensure_runtime(
        self, user_id: int, workspace_id: str
    ) -> dict[str, Any] | None:
        """Make sure the workspace has a running runtime. ``None`` when it does.

        Every file and execution operation needs one: a brand-new workspace has
        no container at all, so the canonical "write a script, then run it" flow
        would otherwise fail on its first step. Starting is idempotent (a running
        runtime is returned as-is), so this costs one check rather than a
        container each time.

        Expressed as ONE rule for every tool instead of leaving the model to
        learn which operations need a runtime — a model that has to guess
        produces a failed call the user reads as the feature being broken.

        A failure here is returned as a value, never raised: an unreachable
        execution host must reach the model as a sentence it can pass on.
        """
        try:
            await self._workspaces.start_runtime(workspace_id, user_id)
        except Exception as exc:  # noqa: BLE001 - reported as a tool error
            return {
                "error": (
                    "This workspace's runtime could not be started, so nothing "
                    f"was changed: {exc}"
                )
            }
        return None

    # ------------------------------------------------------------------
    # Turn context
    # ------------------------------------------------------------------

    async def resolve_context(
        self, user_id: int, workspace_id: str | None, *, source: str = "selected"
    ) -> tuple[WorkspaceTurnContext, dict[str, Any] | None]:
        """Validate and summarize the workspace this turn is about.

        Returns the context the tools will default to plus a small summary for
        the prompt. A workspace that no longer exists, or that belongs to
        somebody else, yields ``(context, None)`` — the prompt then says so
        rather than naming a workspace the user cannot use.

        This is the one place the *requested* workspace id is turned into
        server-known facts, and it never grants anything: the tool that acts on
        the workspace re-reads it through the service, so a stale or hostile id
        in the request cannot become access.
        """
        if not workspace_id:
            return WorkspaceTurnContext(workspace_id=None, source="none"), None
        context = WorkspaceTurnContext(workspace_id=workspace_id, source=source)
        try:
            detail = await self._workspaces.get_detail(workspace_id, user_id)
        except Exception:  # noqa: BLE001 - any failure means "not available"
            return context, None
        runtime = detail.runtime
        return context, {
            "name": detail.workspace.name,
            "runtime_state": str(runtime.state) if runtime else "STOPPED",
        }

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    async def _list_workspaces(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        """List the user's workspaces.

        Deliberately reports only what this one call has actually read. Runtime
        state is NOT included: obtaining it costs a query per workspace, and
        ``get_workspace`` answers it for the one workspace the model then cares
        about. Guessing "STOPPED" for a running workspace here would be a claim
        the tool has not verified — the same class of untruth as a fabricated
        execution result.
        """
        limit = self._as_int(arguments, "limit", default=20, minimum=1, maximum=_MAX_WORKSPACES)
        workspaces, total = await self._workspaces.list_workspaces(
            user_id, limit=limit, offset=0
        )
        payload = [
            {
                "workspace_id": item.workspace_id,
                "name": item.name,
                "description": item.description,
                "python_version": item.runtime_config.python_version,
                "state": str(item.state),
                "execution_count": item.execution_count,
                "last_activity_at": item.last_activity_at.isoformat()
                if item.last_activity_at
                else None,
            }
            for item in workspaces
        ]
        for item in workspaces[: _MAX_ARTIFACT_WORKSPACES]:
            artifacts.append(
                Artifact(
                    type="workspace",
                    id=item.workspace_id,
                    name=item.name,
                    meta={"state": str(item.state)},
                )
            )
        return {
            "count": len(payload),
            "total": total,
            "workspaces": payload,
            "note": (
                "Call get_workspace for one of these to learn whether its runtime "
                "is running."
            ),
        }

    async def _get_workspace(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        detail = await self._workspaces.get_detail(workspace_id, user_id)
        workspace = detail.workspace
        runtime = detail.runtime
        configuration = await self._workspaces.runtime_configuration(workspace_id, user_id)
        revisions = await self._workspaces.list_revisions(workspace_id, user_id, limit=5)
        executions, _total = await self._executions.list_for_workspace(
            workspace_id=workspace_id, user_id=user_id, limit=_MAX_RECENT_EXECUTIONS, offset=0
        )
        artifacts.append(
            Artifact(
                type="workspace",
                id=workspace.workspace_id,
                name=workspace.name,
                meta={
                    "state": str(workspace.state),
                    "runtime_state": str(runtime.state) if runtime else "STOPPED",
                },
            )
        )
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "description": workspace.description,
            "state": str(workspace.state),
            "runtime_state": str(runtime.state) if runtime else "STOPPED",
            "runtime_failure_reason": runtime.failure_reason if runtime else None,
            "python_version": configuration["config"].python_version,
            "uv_version": configuration["config"].uv_version,
            "base_image": configuration["config"].image_ref,
            "resource_profile": configuration["config"].resource_profile,
            "reusable_snapshot": configuration.get("reusable_revision_id") is not None,
            "latest_snapshot_status": str(revisions[0].status) if revisions else None,
            "running_executions": detail.active_executions,
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
                "Files and pyproject.toml/uv.lock live on a durable volume and "
                "survive stopping and restarting. Installed packages survive an "
                "ordinary stop/start, or are restored from a snapshot. Running "
                "processes and in-memory variables are never preserved."
            ),
        }

    async def _create_workspace(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        name = self._as_str(arguments, "name")
        if not name:
            return {"error": "A workspace name is required."}
        description = self._as_str(arguments, "description") or ""
        workspace = await self._workspaces.create_workspace(
            user_id=user_id, name=name, description=description
        )
        artifacts.append(
            Artifact(
                type="workspace",
                id=workspace.workspace_id,
                name=workspace.name,
                meta={"state": str(workspace.state)},
            )
        )
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "runtime_state": "STOPPED",
            "note": (
                "The runtime is not started. It starts automatically the first "
                "time code is run in this workspace."
            ),
        }

    async def _list_files(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        path = self._as_str(arguments, "path") or "."
        failure = await self._ensure_runtime(user_id, workspace_id)
        if failure is not None:
            return failure
        entries = await self._workspaces.list_workspace_files(
            workspace_id, user_id, path=path
        )
        listed = entries[:_MAX_FILE_ENTRIES]
        artifact = await self._workspace_artifact(user_id, workspace_id)
        if artifact is not None:
            artifacts.append(artifact)
        return {
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

    async def _read_file(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        path = self._as_str(arguments, "path")
        if not path:
            return {"error": "A file path is required."}
        failure = await self._ensure_runtime(user_id, workspace_id)
        if failure is not None:
            return failure
        data = await self._workspaces.read_workspace_file(workspace_id, user_id, path=path)
        if len(data) > _MAX_READ_BYTES:
            data = data[:_MAX_READ_BYTES]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "error": (
                    f"{path} is not a text file, so it cannot be read here. It is "
                    "still in the workspace."
                )
            }
        text = redact_secrets(text)
        artifacts.append(
            Artifact(
                type="workspace_file",
                id=f"{workspace_id}:{path}",
                name=path.rsplit("/", 1)[-1],
                meta={"workspace_id": workspace_id, "path": path},
            )
        )
        return {"path": path, "char_count": len(text), "content": text}

    async def _write_file(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        path = self._as_str(arguments, "path")
        content = arguments.get("content")
        if not path:
            return {"error": "A file path is required."}
        if not isinstance(content, str):
            return {"error": "The 'content' argument must be a string."}
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return {
                "error": (
                    f"That file is {len(encoded)} bytes, over the "
                    f"{_MAX_WRITE_BYTES}-byte limit for a single write. Split it "
                    "into several files or write it in parts."
                )
            }
        failure = await self._ensure_runtime(user_id, workspace_id)
        if failure is not None:
            return failure
        await self._workspaces.write_workspace_file(
            workspace_id, user_id, path=path, data=encoded
        )
        artifacts.append(
            Artifact(
                type="workspace_file",
                id=f"{workspace_id}:{path}",
                name=path.rsplit("/", 1)[-1],
                meta={"workspace_id": workspace_id, "path": path},
            )
        )
        return {"path": path, "bytes_written": len(encoded)}

    async def _run_python(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        source = arguments.get("source")
        if not isinstance(source, str) or not source.strip():
            return {"error": "A non-empty 'source' is required."}
        mode = self._parse_mode(arguments.get("mode"))
        if mode is None:
            return {
                "error": "mode must be one of 'script', 'command' or 'project'.",
            }
        timeout = self._as_int(
            arguments, "timeout_seconds", default=self._default_timeout, minimum=1, maximum=3600
        )

        execution = await self._executions.submit(
            workspace_id=workspace_id,
            user_id=user_id,
            mode=mode,
            source=source,
            timeout_seconds=timeout,
        )
        artifacts.append(
            Artifact(
                type="execution",
                id=execution.execution_id,
                name=mode.value.lower(),
                meta={"workspace_id": workspace_id, "status": str(execution.status)},
            )
        )

        settled = await self._await_short(execution, user_id)
        if settled is None:
            # Still queued or running. Deliberately NOT an error: the work is
            # durable and the model is being told how to follow it up.
            return {
                "execution_id": execution.execution_id,
                "status": "running",
                "note": (
                    "The run is still going (dependency installs and real "
                    "programs take longer than a single reply). Call "
                    "get_execution with this execution_id to check it, or tell "
                    "the user it is running and that they can watch it on the "
                    "workspace page."
                ),
            }
        return self._execution_payload(settled, full_output=True)

    async def _await_short(self, execution: Any, user_id: int) -> Any | None:
        """Poll briefly for a fast result, then give up and return ``None``.

        Bounded twice over — by elapsed time AND by a poll count — so a slow
        database cannot turn this into an unbounded loop inside a chat request.
        """
        deadline = _FAST_RESULT_WAIT_SECONDS
        waited = 0.0
        while waited < deadline:
            await asyncio.sleep(_FAST_RESULT_POLL_SECONDS)
            waited += _FAST_RESULT_POLL_SECONDS
            latest = await self._executions.get(execution.execution_id, user_id)
            if latest.is_terminal():
                return latest
        return None

    def _execution_payload(self, execution: Any, *, full_output: bool) -> dict[str, Any]:
        """The model-facing view of one execution, with output bounded+redacted."""
        stdout = redact_secrets(execution.stdout_excerpt or "")
        stderr = redact_secrets(execution.stderr_excerpt or "")
        if full_output:
            stdout, out_cut = bound_text(stdout)
            stderr, err_cut = bound_text(stderr)
        else:
            stdout, out_cut = bound_text(stdout, limit=800)
            stderr, err_cut = bound_text(stderr, limit=800)
        payload: dict[str, Any] = {
            "execution_id": execution.execution_id,
            "status": str(execution.status),
            "mode": str(execution.mode),
            "exit_code": execution.exit_code,
            "duration_ms": execution.duration_ms,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": out_cut or err_cut,
            "error": execution.error,
            "artifacts": [
                {"path": item.path, "size_bytes": item.size_bytes}
                for item in execution.artifacts
            ],
        }
        if execution.status is ExecutionStatus.SUCCEEDED:
            payload["note"] = (
                "The program ran. A non-zero exit_code means the program itself "
                "reported a problem — check stderr before describing the result."
            )
        elif execution.status is ExecutionStatus.TIMED_OUT:
            payload["note"] = "It hit its time limit and was stopped."
        elif execution.status is ExecutionStatus.FAILED:
            payload["note"] = "It failed before or while running; see error/stderr."
        return payload

    async def _get_execution(self, user_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = self._as_str(arguments, "execution_id")
        if not execution_id:
            return {"error": "An execution_id is required."}
        execution = await self._executions.get(execution_id, user_id)
        return self._execution_payload(execution, full_output=True)

    async def _cancel_execution(self, user_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
        execution_id = self._as_str(arguments, "execution_id")
        if not execution_id:
            return {"error": "An execution_id is required."}
        execution = await self._executions.cancel(execution_id, user_id)
        return self._execution_payload(execution, full_output=False)

    async def _save_environment(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        context: WorkspaceTurnContext,
    ) -> dict[str, Any]:
        workspace_id, error = await self._require_workspace_id(user_id, arguments, context)
        if error is not None:
            return error
        assert workspace_id is not None
        revision = await self._workspaces.save_environment(workspace_id, user_id)
        artifact = await self._workspace_artifact(user_id, workspace_id)
        if artifact is not None:
            artifacts.append(artifact)
        return {
            "revision_id": revision.revision_id,
            "status": str(revision.status),
            "note": (
                "The snapshot is built in the background. It becomes READY when "
                "the runtime has been committed, and the next start will restore "
                "the installed packages from it."
            ),
        }

    # ------------------------------------------------------------------
    # Argument helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_str(arguments: dict[str, Any], key: str) -> str | None:
        value = arguments.get(key)
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _as_int(
        arguments: dict[str, Any],
        key: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        """A bounded integer from an untrusted argument.

        Clamped rather than rejected: the model asking for ``limit=500`` should
        get 50 results, not an error it will retry with the same value. A
        non-numeric value falls back to the default for the same reason.
        """
        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _parse_mode(value: Any) -> ExecutionMode | None:
        if value is None:
            return ExecutionMode.SCRIPT
        if not isinstance(value, str):
            return None
        try:
            return ExecutionMode(value.strip().upper())
        except ValueError:
            return None


def build_workspace_context(
    context: WorkspaceTurnContext, summary: Mapping[str, Any] | None
) -> str:
    """The system-prompt line telling the model which workspace is active.

    Server-authored and explicit, so the model is not left to infer the target
    from conversational text — the failure mode the brief calls out. It also
    states the rule that matters: the id is context, and every tool re-checks it.
    """
    if summary is None:
        if context.workspace_id:
            return (
                "A workspace was selected for this conversation but it is no "
                "longer available to this user. Do not assume access: call "
                "list_workspaces and ask the user which to use."
            )
        return (
            "No cloud workspace is selected for this conversation. If the user "
            "asks you to run code or work with workspace files, call "
            "list_workspaces first and ask them which workspace to use, or offer "
            "to create one. Never assume a workspace."
        )
    name = summary.get("name", "unnamed")
    state = summary.get("runtime_state", "STOPPED")
    return (
        f"The active cloud workspace for this conversation is {name!r} "
        f"(workspace_id: {context.workspace_id}, runtime: {state}). Tools default "
        "to this workspace when you omit workspace_id. This is context, not "
        "permission: every tool re-checks that the user owns it."
    )
