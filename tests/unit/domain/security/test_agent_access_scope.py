"""The rules that decide how far an agent's consent reaches.

These are small functions with a large blast radius: each one is the difference
between an agent confined to one folder and an agent holding the user's whole
Drive. The fail-closed direction is the property under test throughout — the
question is never "does it parse?" but "what happens when it cannot?".
"""

from src.domain.security.enitities.agent_grant import AgentGrant
from src.domain.security.value_object.agent_access_scope import (
    FolderAccess,
    HistoryScope,
    coerce_folder_access,
    coerce_history_scope,
    folder_scope_is_usable,
    is_folder_restricted,
)
from src.domain.security.value_object.agent_scope import AgentScope


class TestCoerceFolderAccess:
    def test_reads_the_two_known_values(self) -> None:
        assert coerce_folder_access("ALL") is FolderAccess.ALL
        assert coerce_folder_access("FOLDER") is FolderAccess.FOLDER

    def test_passes_an_enum_through(self) -> None:
        assert coerce_folder_access(FolderAccess.FOLDER) is FolderAccess.FOLDER

    def test_unreadable_value_fails_closed_to_restricted(self) -> None:
        # Not to ALL: a column we cannot parse must not become full-Drive access
        # for an agent the user deliberately confined.
        for value in (None, "", "folder", "EVERYTHING", 7, object()):
            assert coerce_folder_access(value) is FolderAccess.FOLDER


class TestCoerceHistoryScope:
    def test_reads_the_two_known_values(self) -> None:
        assert coerce_history_scope("AGENT") is HistoryScope.AGENT
        assert coerce_history_scope("ALL") is HistoryScope.ALL

    def test_unreadable_value_fails_closed_to_agent_only(self) -> None:
        for value in (None, "", "all", "EVERYTHING", 7):
            assert coerce_history_scope(value) is HistoryScope.AGENT


class TestFolderScopeIsUsable:
    def test_whole_drive_needs_no_folder(self) -> None:
        assert folder_scope_is_usable(FolderAccess.ALL, None) is True

    def test_a_restricted_grant_with_a_folder_is_usable(self) -> None:
        assert folder_scope_is_usable(FolderAccess.FOLDER, "folder-1") is True

    def test_a_restricted_grant_without_a_folder_is_not(self) -> None:
        # The case that matters: the folder was deleted and the reference was
        # cleared. The agent must be denied, NOT promoted to the whole Drive.
        assert folder_scope_is_usable(FolderAccess.FOLDER, None) is False
        assert folder_scope_is_usable(FolderAccess.FOLDER, "") is False


class TestIsFolderRestricted:
    def test_only_the_folder_mode_is_restricted(self) -> None:
        assert is_folder_restricted(FolderAccess.FOLDER, "f") is True
        assert is_folder_restricted(FolderAccess.ALL, None) is False


class TestAgentGrantFolderBinding:
    def _grant(self, **kwargs) -> AgentGrant:
        base = {
            "id": "g1",
            "user_id": 1,
            "client_id": "c1",
            "client_name": "Claude",
            "scopes": (AgentScope.DOCUMENTS_READ,),
        }
        base.update(kwargs)
        return AgentGrant(**base)

    def test_defaults_are_unchanged_behaviour(self) -> None:
        # A grant built without the new fields must behave exactly as it did
        # before folder scoping existed: whole Drive, narrow history.
        grant = self._grant()
        assert grant.folder_access is FolderAccess.ALL
        assert grant.folder_id is None
        assert grant.history_scope is HistoryScope.AGENT
        assert grant.is_folder_restricted() is False

    def test_a_bound_grant_reports_itself_restricted(self) -> None:
        grant = self._grant(folder_access=FolderAccess.FOLDER, folder_id="folder-1")
        assert grant.is_folder_restricted() is True
