"""Domain tests for the assistant error family.

The presentation layer catches these by base class in some places and by
subclass in others, so the hierarchy is part of the contract: a new deletion
error that did not inherit :class:`AssistantError` would silently stop being
translated into an HTTP status and leak as a 500.
"""

import pytest

from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantConversationNotFound,
    AssistantDeletionNotFound,
    AssistantError,
)


def test_deletion_not_found_is_part_of_the_assistant_error_family() -> None:
    error = AssistantDeletionNotFound("no proposal")
    assert isinstance(error, AssistantError)
    # Distinct from the conversation error: the router maps both, but the
    # service must be able to tell "no such chat" from "no such proposal".
    assert not isinstance(error, AssistantConversationNotFound)


def test_deletion_not_found_preserves_its_message() -> None:
    """The router puts this straight into the JSON ``detail``, so it must survive."""
    with pytest.raises(AssistantDeletionNotFound) as raised:
        raise AssistantDeletionNotFound("There is no deletion waiting.")
    assert str(raised.value) == "There is no deletion waiting."
