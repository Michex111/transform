"""The documented tool surface must match the registered one.

Tool names and scope strings are written down in four places: the server's
``INSTRUCTIONS``, ``docs/mcp.md``, the operator prompt in
``web/src/lib/mcpAgentPrompt.ts``, and the public MCP page. They used to be kept
in sync by hand, which is how the public page came to advertise "seven tools"
and four scopes while the server served 24 tools and six scopes.

This test compares the *registered* tools (the only source of truth) with the
copies that a human or a model will actually read. It deliberately does not
assert on prose — only on identifiers, which cannot be paraphrased.
"""

import asyncio
from pathlib import Path

from mcp.server import MCPServer

from src.domain.security.value_object.agent_scope import ALL_SCOPES
from src.presentation.mcp.tools import INSTRUCTIONS, register_tools

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "mcp.md"
WEB_PROMPT = ROOT / "web" / "src" / "lib" / "mcpAgentPrompt.ts"


def _registered_tool_names() -> list[str]:
    server = MCPServer("docs-sync")
    register_tools(server)
    tools = asyncio.run(server.list_tools())
    return [tool.name for tool in tools]


def test_every_registered_tool_is_documented() -> None:
    names = _registered_tool_names()
    assert len(names) == len(set(names)), "tool names must be unique"

    docs = DOCS.read_text(encoding="utf-8")
    web = WEB_PROMPT.read_text(encoding="utf-8")
    missing_docs = [name for name in names if f"`{name}`" not in docs and name not in docs]
    missing_web = [name for name in names if f"`{name}`" not in web and name not in web]
    assert not missing_docs, f"docs/mcp.md is missing: {sorted(missing_docs)}"
    assert not missing_web, f"mcpAgentPrompt.ts is missing: {sorted(missing_web)}"


def test_every_scope_is_documented() -> None:
    docs = DOCS.read_text(encoding="utf-8")
    web = WEB_PROMPT.read_text(encoding="utf-8")
    for scope in ALL_SCOPES:
        assert scope.value in docs, f"docs/mcp.md does not mention {scope.value}"
        assert scope.value in web, f"mcpAgentPrompt.ts does not mention {scope.value}"


def test_instructions_point_at_the_text_reader() -> None:
    """An agent must be told to use read_file_text, not base64, to read."""
    assert "read_file_text" in INSTRUCTIONS
    assert "read_conversion_output" in INSTRUCTIONS
    assert "copy_file_to_workspace" in INSTRUCTIONS
