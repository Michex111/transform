"""The agent must not be promised attachment or link capabilities it lacks."""

from src.presentation.mcp.tools import INSTRUCTIONS


def test_instructions_describe_current_transfer_limitations() -> None:
    assert "cannot access a chat attachment" in INSTRUCTIONS
    # The byte path an attachment needs: a single-use link, not a rebuilt file.
    assert "create_upload_link" in INSTRUCTIONS
    assert "get_upload_status" in INSTRUCTIONS
    assert "Never claim an upload happened until get_upload_status says COMPLETED" in INSTRUCTIONS
    assert "short-lived bearer capabilities" in INSTRUCTIONS
    assert "do not invent one" in INSTRUCTIONS
