"""Domain tests for the durable workspace aggregate.

These are pure: no database, no app, no provider. They pin the invariants the
API and the repository are allowed to rely on.
"""

import pytest

from src.domain.workspaces.entities.workspace import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    Workspace,
)
from src.domain.workspaces.exceptions import InvalidWorkspaceError
from src.domain.workspaces.value_object.workspace_state import WorkspaceState


def _workspace(**overrides) -> Workspace:
    values = {"workspace_id": "ws-1", "user_id": 7, "name": "Analytics"}
    values.update(overrides)
    return Workspace(**values)


class TestNameValidation:
    def test_name_whitespace_is_collapsed(self):
        assert _workspace(name="  data    science  ").name == "data science"

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t", None])
    def test_an_empty_name_is_rejected(self, bad):
        with pytest.raises(InvalidWorkspaceError):
            _workspace(name=bad)

    def test_a_name_longer_than_the_limit_is_rejected(self):
        with pytest.raises(InvalidWorkspaceError):
            _workspace(name="x" * (MAX_NAME_LENGTH + 1))

    def test_a_name_at_the_limit_is_accepted(self):
        assert len(_workspace(name="x" * MAX_NAME_LENGTH).name) == MAX_NAME_LENGTH

    def test_a_description_longer_than_the_limit_is_rejected(self):
        with pytest.raises(InvalidWorkspaceError):
            _workspace(description="x" * (MAX_DESCRIPTION_LENGTH + 1))

    def test_a_negative_execution_count_is_rejected(self):
        with pytest.raises(InvalidWorkspaceError):
            _workspace(execution_count=-1)


class TestStateMachine:
    def test_a_new_workspace_is_active_and_accepts_executions(self):
        workspace = _workspace()
        assert workspace.state is WorkspaceState.ACTIVE
        assert workspace.accepts_executions() is True

    def test_archiving_keeps_the_workspace_but_refuses_executions(self):
        workspace = _workspace()
        workspace.archive()
        assert workspace.state is WorkspaceState.ARCHIVED
        assert workspace.accepts_executions() is False

    def test_archiving_is_reversible(self):
        workspace = _workspace()
        workspace.archive()
        workspace.reactivate()
        assert workspace.state is WorkspaceState.ACTIVE

    def test_delete_is_a_two_step_transition(self):
        workspace = _workspace()
        workspace.begin_delete()
        assert workspace.state is WorkspaceState.DELETING
        workspace.abort_delete()
        assert workspace.state is WorkspaceState.ARCHIVED

    def test_a_workspace_being_deleted_cannot_be_reactivated_directly(self):
        """Recovery from a failed teardown must land in ARCHIVED, not ACTIVE.

        A user retrying a delete is describing the state they want; silently
        re-activating the workspace would undo an intent they expressed.
        """
        workspace = _workspace()
        workspace.begin_delete()
        with pytest.raises(InvalidWorkspaceError):
            workspace.reactivate()

    def test_reactivating_an_active_workspace_is_illegal(self):
        with pytest.raises(InvalidWorkspaceError):
            _workspace().reactivate()


class TestEdits:
    def test_rename_applies_the_same_validation_as_construction(self):
        workspace = _workspace()
        workspace.rename("  Renamed   workspace ")
        assert workspace.name == "Renamed workspace"
        with pytest.raises(InvalidWorkspaceError):
            workspace.rename("   ")

    def test_description_can_be_cleared(self):
        workspace = _workspace(description="hello")
        workspace.update_description(None)
        assert workspace.description == ""

    def test_touch_records_last_activity(self):
        workspace = _workspace()
        assert workspace.last_activity_at is None
        workspace.touch()
        assert workspace.last_activity_at is not None
