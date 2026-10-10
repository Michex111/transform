"""The ``JobOrigin`` value object and its defensive parser.

``JobOrigin`` is persisted domain state, not a presentation detail: the worker's
credit spend order depends on it. The parser is the part that must never raise —
it is fed values from a database column and from Redis stream entries that can
both predate the field.
"""

import pytest

from src.domain.conversions.value_object.job_origin import JobOrigin, coerce_job_origin


def test_members_are_exactly_web_api_mcp_and_guest() -> None:
    """``MCP`` is deliberately separate from ``API``, not folded into it.

    An agent acting under an OAuth grant is a different actor from an API-key
    caller, and the agent-facing ``get_conversion_history`` tool answers "what
    did *this kind of actor* convert?" by filtering on this value. Recording an
    agent's conversions as ``API`` would make that question unanswerable.

    The set is asserted exactly so that adding an origin is a decision someone
    makes here, with a docstring, rather than a silent widening.
    """
    assert {member.value for member in JobOrigin} == {"WEB", "API", "MCP", "GUEST"}


def test_origin_is_a_str_enum_so_a_plain_string_column_round_trips() -> None:
    """The DB column is a String(16), not a native enum, on purpose."""
    assert isinstance(JobOrigin.API, str)
    assert JobOrigin.API == "API"


@pytest.mark.parametrize("raw", ["API", "api", " Api ", "api"])
def test_coerce_accepts_any_case_and_surrounding_whitespace(raw: str) -> None:
    assert coerce_job_origin(raw) is JobOrigin.API


@pytest.mark.parametrize("raw", [None, "", "LEGACY", "CLI", 7, object()])
def test_coerce_degrades_anything_unrecognised_to_web(raw: object) -> None:
    """A missing/garbled origin must default, not raise.

    An old worker's message and an entry already sitting in a Redis stream both
    carry no ``origin`` field; raising would make the job undeliverable.
    """
    assert coerce_job_origin(raw) is JobOrigin.WEB


def test_coerce_passes_an_existing_member_through_unchanged() -> None:
    assert coerce_job_origin(JobOrigin.GUEST) is JobOrigin.GUEST
