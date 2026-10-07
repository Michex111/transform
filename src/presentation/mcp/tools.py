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

There is deliberately **no** tool for uploading arbitrary content, and none for
starting a conversion *without* an owned ``file_id``. Both would be a way to
make Transform read or write something the user never authorized.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import ToolAnnotations

from src.application.services.mcp_toolbox import MCPToolBox, MCPToolContext
from src.domain.security.value_object.agent_scope import normalize_scopes
from src.presentation.mcp.dependencies import open_mcp_scope

#: Shown to the agent on initialize. Kept short and factual: it states the
#: workflow and the two things an agent most often gets wrong (conversions are
#: asynchronous; deletes are permanent).
INSTRUCTIONS = """\
Transform converts documents between formats and stores them in the user's Drive.

Workflow for "convert my X and save the result":
  1. list_files  — find the file and its file_id (the user's own files only).
  2. convert_file(file_id, target_format) — starts a background conversion and
     returns a job_id. The original file is never modified.
  3. get_conversion_status(job_id) — poll until status is COMPLETED or FAILED.
  4. save_file(job_id) — save the converted result into the Drive.

Notes:
  - Every tool acts only on the signed-in user's own data. An id that is not
    theirs is reported as "not found", which is intentional.
  - Conversions are asynchronous: convert_file returns immediately.
  - delete_file is permanent and needs the documents.delete permission, which
    is not granted by default. Always confirm with the user first.
  - File contents are not returned by any tool; only metadata. Never claim to
    have read a document's text.
"""


@contextlib.asynccontextmanager
async def _tool_scope() -> AsyncIterator[tuple[MCPToolBox, MCPToolContext]]:
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
    async with open_mcp_scope(user_id) as scope:
        yield scope.toolbox, MCPToolContext(
            user_id=user_id,
            scopes=scopes,
            tier=scope.tier,
            client_id=token.client_id,
            grant_id=str(claims.get("grant_id", "")),
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


__all__ = ["INSTRUCTIONS", "register_tools"]
