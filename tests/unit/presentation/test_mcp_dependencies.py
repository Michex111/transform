"""The MCP service scope's binding resolution.

One property matters here: a grant that cannot be trusted must never be read as
"unrestricted". ``FolderAccess.ALL`` is the whole-Drive default, so an unresolved
grant that quietly kept the defaults would widen a confined agent instead of
denying the call.
"""

from __future__ import annotations

import pytest

from src.domain.security.enitities.agent_grant import AgentGrant
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import AgentScope
from src.presentation.mcp.dependencies import binding_from_grant

USER = 7


def _grant(**overrides) -> AgentGrant:
    values = {
        "id": "grant-1",
        "user_id": USER,
        "client_id": "client-1",
        "client_name": "Agent",
        "scopes": (AgentScope.DOCUMENTS_READ,),
        "folder_access": FolderAccess.FOLDER,
        "folder_id": "folder-1",
        "history_scope": HistoryScope.AGENT,
    }
    values.update(overrides)
    return AgentGrant(**values)


def test_a_matching_grant_supplies_its_binding() -> None:
    folder_access, folder_id, history_scope = binding_from_grant(
        _grant(), USER, "grant-1"
    )
    assert folder_access is FolderAccess.FOLDER
    assert folder_id == "folder-1"
    assert history_scope is HistoryScope.AGENT


def test_a_missing_grant_is_refused_rather_than_widened() -> None:
    with pytest.raises(RuntimeError):
        binding_from_grant(None, USER, "grant-1")


def test_another_users_grant_is_refused() -> None:
    with pytest.raises(RuntimeError):
        binding_from_grant(_grant(), USER + 1, "grant-1")


def test_a_grant_with_a_different_id_is_refused() -> None:
    """Defends against a token whose claim and the loaded row disagree."""

    with pytest.raises(RuntimeError):
        binding_from_grant(_grant(), USER, "grant-2")
