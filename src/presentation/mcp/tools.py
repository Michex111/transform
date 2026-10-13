"""The MCP tool surface exposed to AI agents.

Design rules applied to every tool here:

* **The name is pinned explicitly.** A tool name is an API contract with every
  client configuration in the wild, so renaming the Python function must never
  rename the tool.
* **The docstring is the contract.** It is what the model reads, so each one
  states what the tool does, what it needs, what it returns, whether it writes,
  and — critically — what it cannot reach.
* **Annotations describe, they do not enforce.** ``read_only_hint`` and friends
  let a well-behaved client ask a human before a destructive call; the actual
  gate is the OAuth scope checked in ``MCPToolBox._require``, which the model
  cannot influence.
* **Identity comes from the token, never from an argument.** ``_tool_scope``
  reads the verified access token; no tool accepts a user id, and no tool
  accepts a storage key.

There is deliberately **no** tool for starting a conversion *without* an owned
``file_id``, and no way to name a storage key. ``upload_file`` is the only tool
that introduces content the user did not already have, and it is bounded (a
small size cap) and confined to the connection's allowed folder, because its
content comes from the model.

Every tool is additionally confined to the folder the user chose when they
connected the application (when they chose one): a file outside it is reported
exactly as a file that does not exist, so the confinement cannot be used to
probe for files elsewhere in the Drive.
"""

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations

from src.application.services.mcp_toolbox import MCPToolBox, MCPToolContext
from src.application.services.mcp_workspace_tools import MCPWorkspaceTools
from src.domain.security.value_object.agent_scope import normalize_scopes
from src.presentation.mcp.activity import instrument_tool
from src.presentation.mcp.dependencies import open_mcp_scope

#: Shown to the agent on initialize. Kept short and factual: it states the
#: workflow and the two things an agent most often gets wrong (conversions are
#: asynchronous; deletes are permanent).
#:
#: NOTE: ``docs/mcp.md`` and ``web/src/lib/mcpAgentPrompt.ts`` are the
#: user-facing copies of this text. They are owned by a different workstream —
#: update them there, not here.
INSTRUCTIONS = """\
Transform converts documents between formats and stores them in the user's Drive.

Workflow for "convert my X and save the result":
  1. list_files  — find the file and its file_id (the user's own files only).
  2. convert_file(file_id, target_format) — starts a background conversion and
     returns a job_id. The original file is never modified.
  3. get_conversion_status(job_id) — poll until status is COMPLETED or FAILED.
  4. save_file(job_id) — save the converted result into the Drive.

Other things you can do:
  - download_file(file_id) — read a document's contents (as base64).
  - upload_file(file_name, content_base64) — create a new file in the Drive.
  - get_conversion_history — list recent conversions.
  - get_credits — check the user's remaining credits.

Cloud workspaces (persistent Python environments):
A workspace keeps its files, project configuration and installed packages between
sessions, so work survives you closing the connection. These tools need the
workspaces.read and workspaces.execute permissions, which are separate from the
documents.* ones and are NOT granted by default.

  1. list_workspaces — find one. Ask the user which to use; never assume one.
  2. get_workspace(workspace_id) — runtime state, toolchain and disk budget.
  3. write_workspace_file(workspace_id, path, content) — write real file content
     verbatim (this is also how you create a directory).
  4. run_code(workspace_id, source, mode) — SCRIPT (Python), COMMAND (shell) or
     PROJECT (`sync`, `lock`, `add <package>`). Returns an execution id
     immediately.
  5. get_execution(execution_id) — the status, exit_code, stdout, stderr and
     artifacts. Call this until the status is terminal.
  6. list_artifacts(workspace_id) — files recent runs produced. `stored: true`
     means the file was copied into durable storage and will outlive the runtime;
     `stored: false` means it only ever existed inside the runtime.
  7. create_workspace(name) — only when the user asks for a new one.
  8. list_workspace_files / read_workspace_file — inspect the workspace.

What a workspace does and does not preserve: files, pyproject.toml, uv.lock and
installed packages survive stopping and restarting. Running processes, open
files and in-memory variables do NOT. Never tell a user their Python variables are
still there.

Notes:
  - Every tool acts only on the signed-in user's own data. An id that is not
    theirs is reported as "not found", which is intentional.
  - This connection may be confined to ONE folder of the user's Drive. Files
    outside it are reported as "not found", and folder arguments outside it are
    refused. Do not tell the user a file exists somewhere you cannot reach.
    Workspaces are not in the Drive, so that confinement does not apply to them.
  - Conversions are asynchronous: convert_file returns immediately. So is
    run_code — NEVER say code ran or succeeded until get_execution says so.
  - A workspace's runtime may be created with no outbound network. When that is
    the case, PROJECT operations (`uv add`) are refused with an explanation.
    Plain scripts still work. Report the refusal rather than retrying.
  - delete_file is permanent and needs the documents.delete permission, which
    is not granted by default. Always confirm with the user first. There is no
    tool that deletes a workspace — that is deliberate.
  - Never claim to have read a document's text unless you used download_file.
"""


@contextlib.asynccontextmanager
async def _tool_scope() -> AsyncGenerator[tuple[MCPToolBox, MCPToolContext]]:
    """Resolve the verified caller and the services for this tool call.

    The identity is taken from the OAuth access token the SDK validated and
    stored in the request context. If it is missing the tool raises rather than
    guessing: an MCP tool must never be able to run unauthenticated, and
    ``None`` here means the auth middleware was bypassed (a wiring bug), not
    that the caller is anonymous.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise RuntimeError("MCP tool invoked without an authenticated access token.")
    try:
        user_id = int(token.subject)
    except (TypeError, ValueError) as exc:  # pragma: no cover - provider always writes an int
        raise RuntimeError("MCP access token has a malformed subject.") from exc

    scopes = normalize_scopes(token.scopes or ())
    claims = token.claims or {}
    grant_id = str(claims.get("grant_id", ""))
    async with open_mcp_scope(user_id, grant_id) as scope:
        yield scope.toolbox, MCPToolContext(
            user_id=user_id,
            scopes=scopes,
            tier=scope.tier,
            client_id=token.client_id,
            grant_id=grant_id,
            # The folder confinement and history scope come from the stored
            # grant, loaded by ``open_mcp_scope``. They are part of the token's
            # authority, never a tool argument.
            folder_access=scope.folder_access,
            folder_id=scope.folder_id,
            history_scope=scope.history_scope,
        )


@contextlib.asynccontextmanager
async def _workspace_scope() -> AsyncGenerator[tuple["MCPWorkspaceTools", MCPToolContext]]:
    """Resolve the caller and the workspace tools for this call.

    Same identity resolution as ``_tool_scope``: the caller comes from the
    verified token and nothing else. Kept as a second helper rather than one
    mega-scope so a workspace tool cannot accidentally receive the document
    toolbox (and therefore cannot reach the folder-confinement code path it has
    no business in).
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise RuntimeError("MCP tool invoked without an authenticated access token.")
    try:
        user_id = int(token.subject)
    except (TypeError, ValueError) as exc:  # pragma: no cover - provider writes an int
        raise RuntimeError("MCP access token has a malformed subject.") from exc

    scopes = normalize_scopes(token.scopes or ())
    claims = token.claims or {}
    grant_id = str(claims.get("grant_id", ""))
    async with open_mcp_scope(user_id, grant_id) as scope:
        if scope.workspace_tools is None:  # pragma: no cover - always wired
            raise RuntimeError("Workspace tools are not configured.")
        yield scope.workspace_tools, MCPToolContext(
            user_id=user_id,
            scopes=scopes,
            tier=scope.tier,
            client_id=token.client_id,
            grant_id=grant_id,
            folder_access=scope.folder_access,
            folder_id=scope.folder_id,
            history_scope=scope.history_scope,
        )


def register_tools(mcp: MCPServer) -> None:
    """Register every MCP tool on ``mcp``."""

    # ------------------------------------------------------------------
    # Read-only
    # ------------------------------------------------------------------

    @mcp.tool(
        name="get_supported_conversions",
        title="List supported conversions",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_supported_conversions")
    async def get_supported_conversions(source_format: str | None = None) -> dict[str, Any]:
        """List the file conversions this service can perform.

        Call this before convert_file if you are unsure whether a conversion
        exists (for example "can a .tex file become a .pdf?").

        Args:
            source_format: Optional source extension without a dot, e.g. "docx".
                Omit it to get the full conversion map.

        Returns:
            Without source_format: {"ok": true, "conversions": {"<source>":
            ["<target>", ...]}, "source_format_count", "target_format_count"}.
            With source_format: {"ok": true, "source_format", "target_formats"}.
            On an unknown format: {"ok": false, "error", "hint"}.

        Does not read, modify or create any user data. Requires documents.read.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.get_supported_conversions(ctx, source_format)

    @mcp.tool(
        name="list_files",
        title="List or search the user's files",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("list_files")
    async def list_files(
        query: str | None = None,
        extension: str | list[str] | None = None,
        folder_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List or search the documents in the user's own Transform Drive.

        Searches the whole Drive by default, because a file inside a folder is
        still one of the user's files. Use this first to obtain the file_id that
        the other tools need, and use `query` to find a file by name (e.g.
        "resume").

        Args:
            query: Optional case-insensitive substring of the file name.
            extension: Optional format filter — one extension or a list, e.g.
                "pdf" or ["xlsx", "csv", "ods"]. Category words are NOT
                formats: for "spreadsheets" pass the concrete extensions.
            folder_id: Optional folder id to restrict the listing to one folder.
            limit: Maximum rows to return (1-50, default 20).
            offset: Rows to skip, for paging.

        Returns:
            {"ok": true, "files": [{"file_id", "file_name", "extension",
            "size_bytes", "folder_id", "created_at"}], "total", "returned"}.
            On failure: {"ok": false, "error"}.

        Read-only. Requires documents.read. Files belonging to other accounts are
        never returned and cannot be reached by guessing an id.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.list_files(
                ctx, query=query, extension=extension, folder_id=folder_id,
                limit=limit, offset=offset,
            )

    @mcp.tool(
        name="get_file",
        title="Get details of one file",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_file")
    async def get_file(file_id: str) -> dict[str, Any]:
        """Get the metadata of one file the user owns.

        Use this to confirm a file's name, format and size before converting it.

        Args:
            file_id: The id returned by list_files. It must belong to the
                signed-in user.

        Returns:
            {"ok": true, "file": {"file_id", "file_name", "extension",
            "size_bytes", "folder_id", "created_at"}} or {"ok": false, "error"}.

        Returns metadata only — never the document's contents and never an
        internal storage location. Read-only. Requires documents.read.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.get_file(ctx, file_id)

    @mcp.tool(
        name="download_file",
        title="Download a file's contents",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("download_file")
    async def download_file(file_id: str) -> dict[str, Any]:
        """Return the CONTENTS of one of the user's files, base64-encoded.

        Use this when the user asks you to read, summarise, quote or translate a
        document's text. Conversions only return a new file; they do not expose
        content. For anything but a small document, prefer converting it or
        having the user open it in the Transform web app — a single tool call is
        the wrong channel for a large transfer.

        Args:
            file_id: The id returned by list_files. It must belong to the
                signed-in user.

        Returns:
            {"ok": true, "file_name", "extension", "size_bytes",
            "content_base64"} — decode the base64 to get the raw bytes.
            {"ok": false, "error"} when the file is missing, outside this
            connection's folder, or larger than the size limit for this tool
            (in which case the error says to use the web app).

        Read-only. Requires documents.read. A file that exists but is not
        reachable by this connection is reported exactly as a file that does not
        exist.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.download_file(ctx, file_id)

    @mcp.tool(
        name="get_conversion_status",
        title="Check a conversion",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_conversion_status")
    async def get_conversion_status(job_id: str) -> dict[str, Any]:
        """Check the progress of a conversion started by convert_file.

        Conversions run in the background. Poll this until "status" is
        "COMPLETED" or "FAILED"; a typical document takes a few seconds.

        Args:
            job_id: The job_id returned by convert_file.

        Returns:
            {"ok": true, "job": {"job_id", "status", "progress",
            "source_format", "target_format", "input_file", "output_file",
            "download_available", "error_message", "created_at"}}.
            "status" is one of AWAITING_UPLOAD, PENDING, PROCESSING, COMPLETED,
            FAILED. When FAILED, "error_message" explains why.

        Read-only. Requires documents.read. Only jobs belonging to the signed-in
        user can be inspected.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.get_conversion_status(ctx, job_id)

    @mcp.tool(
        name="get_conversion_history",
        title="List recent conversions",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_conversion_history")
    async def get_conversion_history(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """List the user's recent conversions, newest first.

        How much history is visible depends on the permission this application
        was granted: by default it sees only conversions started through a
        connected application (which includes other apps the user has
        connected), and only if the user explicitly allowed it does it see the
        user's entire conversion history.

        Args:
            limit: Maximum rows to return (1-50, default 20).
            offset: Rows to skip, for paging.

        Returns:
            {"ok": true, "items": [{"job_id", "status", "source_format",
            "target_format", "input_file", "output_file", "created_at",
            "credits_used"}], "total", "returned", "scope"} where "scope" is
            "AGENT" or "ALL". {"ok": false, "error"} on failure.

        Read-only. Requires documents.read. Never returns file contents or
        storage locations.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.get_conversion_history(ctx, limit=limit, offset=offset)

    @mcp.tool(
        name="get_credits",
        title="Check the user's credit balance",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_credits")
    async def get_credits() -> dict[str, Any]:
        """Report the user's remaining conversion credits for the current period.

        Use this before starting a lot of conversions, or when the user asks how
        many credits they have left. Credits reset monthly on paid plans.

        Returns:
            {"ok": true, "balance": <int>, "resets_at": <ISO timestamp|null>}.
            "resets_at" is null when the plan's credits do not reset.
            {"ok": false, "error"} on failure.

        Read-only. Requires documents.read. Reads the signed-in user's own
        balance only.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.get_credits(ctx)

    # ------------------------------------------------------------------
    # Mutating
    # ------------------------------------------------------------------

    @mcp.tool(
        name="convert_file",
        title="Convert a file to another format",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("convert_file")
    async def convert_file(file_id: str, target_format: str) -> dict[str, Any]:
        """Start converting one of the user's files into another format.

        This does NOT modify or replace the original file, and it does not add
        anything to the Drive by itself: it produces a conversion result that
        can be downloaded or promoted into the Drive with save_file. The
        conversion runs in the background.

        Args:
            file_id: The id of an existing file the user owns (from list_files).
            target_format: The destination extension without a dot, e.g. "pdf".
                Use get_supported_conversions to check what is available.

        Returns:
            {"ok": true, "job": {...}, "note"} where job.job_id is passed to
            get_conversion_status and save_file. On an unsupported pair:
            {"ok": false, "error", "source_format", "supported_target_formats"}.

        Creates a new conversion; the original file is preserved. Requires
        documents.convert.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.convert_file(ctx, file_id, target_format)

    @mcp.tool(
        name="save_file",
        title="Save a conversion result to the Drive",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("save_file")
    async def save_file(
        job_id: str,
        folder_id: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """Save the output of a COMPLETED conversion as a new file in the Drive.

        Only a conversion the user owns, that has finished successfully, can be
        saved. Arbitrary content cannot be uploaded through this tool — the
        bytes must already be a document Transform produced.

        Args:
            job_id: The job_id of a COMPLETED conversion from convert_file.
            folder_id: Optional destination folder id. Omit to save to the
                Drive root.
            file_name: Optional name for the saved file. It must include the
                correct extension. Omit to use the name Transform produced
                (which is authoritative — e.g. a multi-page PDF to image job
                produces a .zip).

        Returns:
            {"ok": true, "file": {"file_id", ...}, "note"} or
            {"ok": false, "error"}.

        Creates a new file and leaves the conversion result in place. Nothing is
        overwritten or deleted. Requires documents.write.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.save_file(
                ctx, job_id=job_id, folder_id=folder_id, file_name=file_name
            )

    @mcp.tool(
        name="upload_file",
        title="Upload a new file into the Drive",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("upload_file")
    async def upload_file(
        file_name: str,
        content_base64: str,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a NEW file in the user's Drive from content you provide.

        Generate the file's bytes, base64-encode them, and pass them here. This
        is how you save a document you authored (for example a report you
        generated as text) into the user's Drive, unlike save_file which only
        promotes a conversion result.

        Args:
            file_name: The name for the new file, including its extension
                (e.g. "report.pdf" or "notes.md").
            content_base64: The file's raw bytes, base64-encoded. This is
                supplied by you and is length-limited (a few tens of MB); for
                anything larger ask the user to upload it in the web app.
            folder_id: Optional destination folder id. Omit to place the file in
                the connection's folder (or the Drive root for an unrestricted
                connection). A folder outside this connection's allowed folder
                is refused.

        Returns:
            {"ok": true, "file": {"file_id", "file_name", "extension",
            "size_bytes", "folder_id"}} or {"ok": false, "error"}.

        Creates a new file; nothing is overwritten. Requires documents.write.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.upload_file(
                ctx, file_name=file_name, content_base64=content_base64,
                folder_id=folder_id,
            )

    @mcp.tool(
        name="delete_file",
        title="Permanently delete a file",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("delete_file")
    async def delete_file(file_id: str) -> dict[str, Any]:
        """Permanently delete one of the user's files. This cannot be undone.

        Confirm with the user, naming the exact file, before calling this. It
        requires the documents.delete permission, which is never granted unless
        the user explicitly selected it when connecting the application; if it
        is missing the call returns an error and nothing is deleted.

        Args:
            file_id: The id of a file the user owns (from list_files).

        Returns:
            {"ok": true, "deleted_file_id", "file_name"} or {"ok": false,
            "error"} — including {"ok": false, "required_scope":
            "documents.delete"} when permission was not granted.

        DESTRUCTIVE and irreversible: both the file record and its stored
        contents are removed. Requires documents.delete.
        """
        async with _tool_scope() as (toolbox, ctx):
            return await toolbox.delete_file(ctx, file_id)

    # ------------------------------------------------------------------
    # Cloud workspaces
    # ------------------------------------------------------------------
    #
    # Separated by scope rather than grouped by resource: the read-only tools
    # need workspaces.read, and everything that can write or run code needs
    # workspaces.execute. An agent connected for reading cannot reach the
    # execution tools even if it asks for them, because the refusal is a scope
    # check in the tool body — not a description in a prompt.

    @mcp.tool(
        name="list_workspaces",
        title="List cloud workspaces",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("list_workspaces")
    async def list_workspaces(limit: int = 20) -> dict[str, Any]:
        """List the user's cloud workspaces.

        A workspace is a persistent Python environment that keeps its files and
        installed packages between sessions. Read-only.

        Args:
            limit: Maximum workspaces to return (default 20, maximum 50).

        Returns:
            {"ok": true, "total", "workspaces": [{workspace_id, name,
            description, python_version, state, execution_count}]} or
            {"ok": false, "error", "required_scope": "workspaces.read"}.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.list_workspaces(ctx, limit=limit)

    @mcp.tool(
        name="get_workspace",
        title="Get one cloud workspace",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_workspace")
    async def get_workspace(workspace_id: str) -> dict[str, Any]:
        """Status of one workspace: runtime state, toolchain, disk budget, runs.

        Args:
            workspace_id: The id of a workspace the user owns (from
                list_workspaces).

        Returns:
            {"ok": true, "runtime_state", "python_version", "uv_version",
            "disk", "recent_executions", ...} or {"ok": false, "error"}.
            Requires workspaces.read.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.get_workspace(ctx, workspace_id)

    @mcp.tool(
        name="create_workspace",
        title="Create a cloud workspace",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("create_workspace")
    async def create_workspace(name: str, description: str = "") -> dict[str, Any]:
        """Create a workspace. This starts no compute and is reversible.

        Args:
            name: A short human name for the workspace.
            description: Optional note about what it is for.

        Returns:
            {"ok": true, "workspace_id", "name", "runtime_state": "STOPPED"}
            or {"ok": false, "error"}. Requires workspaces.execute.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.create_workspace(ctx, name, description)

    @mcp.tool(
        name="list_workspace_files",
        title="List workspace files",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("list_workspace_files")
    async def list_workspace_files(workspace_id: str, path: str = ".") -> dict[str, Any]:
        """List one directory inside a workspace.

        The workspace's runtime is started automatically if it is not running.

        Args:
            workspace_id: The workspace to list.
            path: Directory relative to the workspace root; omit for the root.

        Returns:
            {"ok": true, "path", "count", "entries": [{name, path, is_dir,
            size_bytes}]} or {"ok": false, "error"}. Requires workspaces.read.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.list_workspace_files(ctx, workspace_id, path)

    @mcp.tool(
        name="read_workspace_file",
        title="Read a workspace file",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("read_workspace_file")
    async def read_workspace_file(workspace_id: str, path: str) -> dict[str, Any]:
        """Read a text file from a workspace. Binary files are refused.

        Args:
            workspace_id: The workspace to read from.
            path: File path relative to the workspace root.

        Returns:
            {"ok": true, "path", "content", "size_bytes", "truncated"} or
            {"ok": false, "error"}. Requires workspaces.read.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.read_workspace_file(ctx, workspace_id, path)

    @mcp.tool(
        name="write_workspace_file",
        title="Write a workspace file",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("write_workspace_file")
    async def write_workspace_file(
        workspace_id: str, path: str, content: str
    ) -> dict[str, Any]:
        """Write a text file into a workspace, creating parent directories.

        The content is written verbatim, so pass real file content rather than a
        description of it. The runtime is started automatically if needed.

        Args:
            workspace_id: The workspace to write into.
            path: File path relative to the workspace root.
            content: The exact file content (maximum 2 MB).

        Returns:
            {"ok": true, "path", "bytes_written"} or {"ok": false, "error"}.
            Requires workspaces.execute.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.write_workspace_file(ctx, workspace_id, path, content)

    @mcp.tool(
        name="run_code",
        title="Run code in a workspace",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("run_code")
    async def run_code(
        workspace_id: str,
        source: str,
        mode: str = "SCRIPT",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run Python, a shell command, or a `uv` operation in a workspace.

        Asynchronous: this returns as soon as the run is queued or started, with
        an execution id. Call get_execution to learn whether it succeeded — do
        not assume success from this response.

        Args:
            workspace_id: The workspace to run in.
            source: The Python code, the shell command line, or a `uv`
                operation (`sync`, `lock`, `add <package>`).
            mode: SCRIPT (default), COMMAND, or PROJECT.
            timeout_seconds: Time limit, 1-3600 (default 300).

        Returns:
            {"ok": true, "execution_id", "status"} or {"ok": false, "error"}.
            Requires workspaces.execute.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.run_code(
                ctx, workspace_id, source, mode=mode, timeout_seconds=timeout_seconds
            )

    @mcp.tool(
        name="get_execution",
        title="Get an execution's result",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("get_execution")
    async def get_execution(execution_id: str) -> dict[str, Any]:
        """Status, exit code and bounded output of one execution.

        Args:
            execution_id: The id returned by run_code.

        Returns:
            {"ok": true, "status", "exit_code", "stdout", "stderr",
            "artifacts"} or {"ok": false, "error"}. Requires
            workspaces.read.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.get_execution(ctx, execution_id)

    @mcp.tool(
        name="list_artifacts",
        title="List workspace artifacts",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @instrument_tool("list_artifacts")
    async def list_artifacts(workspace_id: str) -> dict[str, Any]:
        """Files recent executions produced, and whether they are stored.

        `stored: true` means the bytes were copied into durable storage, so the
        file survives the runtime being replaced. `stored: false` means it only
        ever existed inside the runtime.

        Args:
            workspace_id: The workspace whose artifacts to list.

        Returns:
            {"ok": true, "artifacts": [{execution_id, path, size_bytes,
            stored}]} or {"ok": false, "error"}. Requires workspaces.read.
        """
        async with _workspace_scope() as (tools, ctx):
            return await tools.list_artifacts(ctx, workspace_id)


__all__ = ["INSTRUCTIONS", "register_tools"]
