"""Tests for the assistant prompt text.

The system prompt is the assistant's product behaviour: what it may claim and
which tools it must use. These assertions are deliberately about *meaning*
(the presence of the rules), not exact wording, so the copy can be edited
freely while the safety-critical instructions cannot quietly disappear.
"""

from src.application.services.assistant_prompts import SYSTEM_PROMPT


def test_the_prompt_forbids_claiming_a_file_was_deleted() -> None:
    """The model can only propose a deletion, so it must never claim one happened.

    If this rule is lost, the assistant tells the user a file is gone when it is
    still there — or worse, trains them to believe deletions are automatic.
    """
    lowered = SYSTEM_PROMPT.lower()
    assert "delete_file" in SYSTEM_PROMPT
    assert "never say or imply" in lowered
    assert "deleted" in lowered
    # It must also tell the model to ask for confirmation rather than act.
    assert "confirm" in lowered


def test_the_prompt_still_requires_tools_before_naming_user_data() -> None:
    """The two load-bearing rules from the module docstring must both survive."""
    assert "ALWAYS use a tool before naming a specific file" in SYSTEM_PROMPT
    assert "list_supported_targets" in SYSTEM_PROMPT
