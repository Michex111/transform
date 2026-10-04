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


def test_the_prompt_requires_a_format_question_to_filter_by_that_format() -> None:
    """The model must filter, not list-everything-then-narrow-in-prose.

    This is the rule that stops unrelated documents being attached: when the
    answer is written from an unfiltered listing, the files chipped onto it come
    from the rows the tool returned rather than from what the prose mentions, so
    "here are your PDFs" arrived with Word documents attached to it.
    """
    # The prompt is wrapped for readability, so a phrase can straddle a newline;
    # flatten whitespace before looking for one, or the assertion tests the
    # line breaks instead of the rule.
    flat = " ".join(SYSTEM_PROMPT.lower().split())
    assert "extension" in flat
    # The failure mode is naming a format and then not filtering by it.
    assert "never list every file and filter them in your reply" in flat
    # …and the prompt must say WHY, or a future edit will drop it as noise.
    assert "attached to your answer" in flat
    assert "do not pad the answer" in flat
    assert "do not mention a file you were not asked about" in flat
