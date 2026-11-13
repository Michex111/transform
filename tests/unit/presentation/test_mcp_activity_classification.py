"""The MCP activity classifier: expected refusals are not malfunctions."""

from src.domain.telemetry.entities.api_request_event import ToolOutcome
from src.presentation.mcp.activity import _classify


def test_a_success_is_success() -> None:
    assert _classify({"ok": True, "files": []}) == (ToolOutcome.SUCCESS, None)


def test_a_scope_refusal_is_denied() -> None:
    outcome, category = _classify(
        {"ok": False, "error": "needs documents.write", "required_scope": "documents.write"}
    )
    assert outcome == ToolOutcome.DENIED
    assert category == "permission_denied"


def test_an_unreadable_grant_is_denied() -> None:
    outcome, category = _classify(
        {"ok": False, "error": "Reconnect the application.", "reconnect_required": True}
    )
    assert outcome == ToolOutcome.DENIED
    assert category == "authorization_changed"


def test_an_expected_refusal_is_not_an_error() -> None:
    """D5: "no such file" and "still processing" are the tool working.

    Counting them as ERROR made an ordinary agent workflow look like an outage on
    the activity dashboard, which is exactly the signal an operator needs to be
    trustworthy.
    """
    outcome, category = _classify({"ok": False, "error": "No such file was found."})
    assert outcome == ToolOutcome.REFUSED
    assert category == "expected_refusal"
    assert outcome in ToolOutcome.ALL
