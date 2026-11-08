"""The agent must not be promised attachment or link capabilities it lacks."""

from src.presentation.mcp.tools import INSTRUCTIONS


def test_instructions_describe_current_transfer_limitations() -> None:
    assert "cannot access a chat attachment" in INSTRUCTIONS
    assert "actual bytes as base64" in INSTRUCTIONS
    assert "short-lived bearer capability" in INSTRUCTIONS
    assert "do not invent one" in INSTRUCTIONS
