"""MCP status exposes capabilities only after a successful completed lookup."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.application.services.mcp.download_capability import DownloadUnavailable
from src.presentation.mcp import tools


class RecordingServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, name: str, **kwargs: Any):
        def register(func):
            self.tools[name] = func
            return func
        return register


def setup_status(monkeypatch, response: dict[str, Any], *, fail: bool = False):
    calls: list[tuple[str, str]] = []

    class Toolbox:
        async def get_conversion_status(self, ctx, job_id):
            assert job_id == "job"
            return response.copy()

    @asynccontextmanager
    async def scope():
        yield Toolbox(), object()

    async def issue(token: str, job_id: str):
        calls.append((token, job_id))
        if fail:
            raise DownloadUnavailable()
        return {"download_url": "https://example.test/download#cap", "expires_in_seconds": 300}

    monkeypatch.setattr(tools, "_tool_scope", scope)
    monkeypatch.setattr(tools, "get_access_token", lambda: SimpleNamespace(token="verified"))
    monkeypatch.setattr(tools, "create_mcp_download_link", issue)
    server = RecordingServer()
    tools.register_tools(server)  # type: ignore[arg-type]
    return server.tools["get_conversion_status"], calls


def test_completed_status_returns_capability(monkeypatch) -> None:
    status, calls = setup_status(monkeypatch, {"ok": True, "job": {"status": "COMPLETED"}})
    result = asyncio.run(status("job"))
    assert result["job"]["status"] == "COMPLETED"
    assert result["download_url"] == "https://example.test/download#cap"
    assert result["expires_in_seconds"] == 300
    assert calls == [("verified", "job")]
    assert "verified" not in repr(result)


@pytest.mark.parametrize("response", [
    {"ok": False, "error": "No such conversion was found."},
    {"ok": False, "error": "Missing scope", "required_scope": "documents.read"},
    {"ok": True, "job": {"status": "PENDING"}},
    {"ok": True, "job": {"status": "PROCESSING"}},
    {"ok": True, "job": {"status": "FAILED"}},
])
def test_no_capability_for_denied_or_noncompleted_status(monkeypatch, response) -> None:
    status, calls = setup_status(monkeypatch, response)
    assert asyncio.run(status("job")) == response
    assert calls == []


def test_unavailable_link_does_not_change_successful_status(monkeypatch) -> None:
    status, calls = setup_status(
        monkeypatch, {"ok": True, "job": {"status": "COMPLETED"}}, fail=True
    )
    result = asyncio.run(status("job"))
    assert result["ok"] is True
    assert "download_url" not in result
    assert "not available" in result["download_note"]
    assert calls == [("verified", "job")]
