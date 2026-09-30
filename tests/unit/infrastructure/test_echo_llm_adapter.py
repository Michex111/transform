"""Unit tests for the offline ("echo") assistant backend.

This backend is what makes the feature work with no API key, so its behaviour is
part of the product rather than a test detail: the rules below are the demo path
(list files → resolve one → act), the honest refusal when nothing matches, and
the structural guarantee that a turn always ends.
"""

import asyncio
import json
from collections.abc import Sequence

import pytest

from src.application.ports.llm_port import (
    LlmMessage,
    LlmStreamChunk,
    LlmToolCall,
    LlmToolSpec,
)
from src.infrastructure.adapters.ai.echo_llm_adapter import EchoLlmAdapter

FILE_ID = "file-1"

TOOLS = [
    LlmToolSpec(name=name, description="", parameters={"type": "object"})
    for name in (
        "list_files",
        "list_supported_targets",
        "summarize_file",
        "start_conversion",
        "list_folders",
    )
]


def _stream(messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec] = tuple(TOOLS)):
    async def _collect() -> list[LlmStreamChunk]:
        chunks: list[LlmStreamChunk] = []
        async for chunk in EchoLlmAdapter().stream(messages=messages, tools=tools):
            chunks.append(chunk)
        return chunks

    return asyncio.run(_collect())


def _reply(messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec] = tuple(TOOLS)) -> str:
    return "".join(chunk.text for chunk in _stream(messages, tools) if chunk.kind == "text")


def _tool_calls(
    messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec] = tuple(TOOLS)
) -> tuple[LlmToolCall, ...]:
    return tuple(
        call
        for chunk in _stream(messages, tools)
        if chunk.response is not None
        for call in chunk.response.tool_calls
    )


def _user(text: str) -> LlmMessage:
    return LlmMessage(role="user", content=text)


def _list_files_result(files: list[dict]) -> LlmMessage:
    return LlmMessage(
        role="tool",
        content=json.dumps({"files": files, "count": len(files)}),
        tool_call_id="call_echo",
        name="list_files",
    )


def _one_file() -> dict:
    return {"file_id": FILE_ID, "file_name": "report.pdf", "extension": "pdf"}


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_model_name_identifies_the_backend() -> None:
    assert EchoLlmAdapter().model == "echo"


def test_an_unmatched_message_explains_the_offline_mode() -> None:
    reply = _reply([_user("Tell me a joke about spreadsheets")])
    assert "offline demo mode" in reply
    assert "files" in reply


def test_a_listing_question_calls_list_files() -> None:
    calls = _tool_calls([_user("What files do I have?")])
    assert [call.name for call in calls] == ["list_files"]
    assert calls[0].arguments["limit"] == 20


# ---------------------------------------------------------------------------
# Ranking the drive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What's the largest file in my drive?",
        "Which of my files are the largest?",
        "show me my biggest file",
    ],
)
def test_a_superlative_asks_for_a_RANKED_whole_drive_listing(question: str) -> None:
    """A superlative is answered by the server ranking the drive, not by a page.

    The rule engine cannot sort anything itself: the whole point of asking the
    tool for ``all_folders`` + ``sort: size`` is that the *database* knows which
    file is biggest. Answering from a listing it ordered itself would make the
    offline backend state a fact it cannot know.
    """
    calls = _tool_calls([_user(question)])
    assert [call.name for call in calls] == ["list_files"]
    arguments = calls[0].arguments
    assert arguments["all_folders"] is True
    assert arguments["sort"] == "size"
    assert arguments["order"] == "desc"


def test_the_superlative_limit_matches_the_number_of_files_asked_about() -> None:
    """"the largest file" is one file; "which of my files are the largest" is a few."""
    singular = _tool_calls([_user("what's my largest file?")])[0].arguments
    plural = _tool_calls([_user("which of my files are the largest?")])[0].arguments
    assert singular["limit"] == 1
    assert plural["limit"] == 3


def test_a_smallest_question_ranks_ascending() -> None:
    calls = _tool_calls([_user("what is my smallest file?")])
    assert calls[0].arguments["order"] == "asc"


def test_a_ranked_size_result_is_answered_as_a_superlative() -> None:
    """The ranking is read from the tool result, never from the user's wording."""
    messages = [
        _user("what's my largest file?"),
        LlmMessage(
            role="tool",
            content=json.dumps(
                {
                    "files": [{"file_id": "f1", "file_name": "video.mkv", "size_bytes": 9_000_000}],
                    "count": 1,
                    "ordered_by": {"key": "size", "direction": "desc"},
                }
            ),
            tool_call_id="call_echo",
            name="list_files",
        ),
    ]
    reply = _reply(messages)
    assert "largest" in reply
    assert "video.mkv" in reply
    assert "8.6 MB" in reply
    # The listing phrasing would be "I found 1 file:" — which answers a question
    # nobody asked.
    assert "I found" not in reply


def test_an_unranked_listing_is_never_described_as_a_superlative() -> None:
    """Without ``ordered_by`` the rows are just a listing, whatever was asked.

    This is the honesty guard: the wording prompts a superlative, but the result
    carries no ranking, so claiming "your largest file" would invent a fact.
    """
    messages = [
        _user("what's my largest file?"),
        _list_files_result([{"file_id": "f1", "file_name": "video.mkv", "size_bytes": 9_000_000}]),
    ]
    reply = _reply(messages)
    assert "largest" not in reply
    assert "I found 1 file:" in reply


def test_a_conversion_request_resolves_the_named_file_first() -> None:
    calls = _tool_calls([_user('Convert "report.pdf" to docx please')])
    assert [call.name for call in calls] == ["list_files"]
    assert calls[0].arguments["query"] == "report.pdf"


def test_a_summary_request_asks_for_the_file_list() -> None:
    calls = _tool_calls([_user("summarize my thesis.pdf")])
    assert [call.name for call in calls] == ["list_files"]


def test_a_formats_question_asks_the_registry() -> None:
    calls = _tool_calls([_user("What can I convert a png to?")])
    assert [call.name for call in calls] == ["list_supported_targets"]
    assert calls[0].arguments == {"source_format": "png"}


def test_a_folder_question_lists_folders() -> None:
    calls = _tool_calls([_user("Show me my folders")])
    assert [call.name for call in calls] == ["list_folders"]


# ---------------------------------------------------------------------------
# The second hop
# ---------------------------------------------------------------------------


def test_one_match_is_enough_to_start_the_conversion() -> None:
    messages = [_user("convert report.pdf to docx"), _list_files_result([_one_file()])]
    calls = _tool_calls(messages)
    assert [call.name for call in calls] == ["start_conversion"]
    assert calls[0].arguments == {"file_id": FILE_ID, "target_format": "docx"}


def test_one_match_is_enough_to_summarise() -> None:
    messages = [_user("summarize report.pdf"), _list_files_result([_one_file()])]
    calls = _tool_calls(messages)
    assert [call.name for call in calls] == ["summarize_file"]
    assert calls[0].arguments == {"file_id": FILE_ID}


def test_several_matches_stop_and_ask() -> None:
    """Ambiguity must not be resolved by guessing — the answer lists the matches."""
    messages = [
        _user("convert report.pdf to docx"),
        _list_files_result(
            [_one_file(), {"file_id": "file-2", "file_name": "report2.pdf", "extension": "pdf"}]
        ),
    ]
    assert _tool_calls(messages) == ()
    assert "report.pdf" in _reply(messages)


def test_no_match_stops_and_says_so() -> None:
    messages = [_user("convert report.pdf to docx"), _list_files_result([])]
    assert _tool_calls(messages) == ()
    assert "couldn't find" in _reply(messages)


def test_a_conversion_without_a_stated_target_does_not_guess_one() -> None:
    messages = [_user("convert report.pdf"), _list_files_result([_one_file()])]
    assert _tool_calls(messages) == ()


def test_the_loop_always_terminates() -> None:
    """After the tool budget is spent the backend answers instead of asking again."""
    messages = [
        _user("convert report.pdf to docx"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=(LlmToolCall(id="c1", name="list_files", arguments={}),),
        ),
        _list_files_result([_one_file()]),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=(LlmToolCall(id="c2", name="list_files", arguments={}),),
        ),
        _list_files_result([_one_file()]),
    ]
    assert _tool_calls(messages) == ()
    assert _reply(messages)


def test_without_offered_tools_it_never_requests_one() -> None:
    assert _tool_calls([_user("convert report.pdf to docx")], tools=()) == ()


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def test_a_started_conversion_is_reported_readably() -> None:
    messages = [
        _user("convert report.pdf to docx"),
        LlmMessage(
            role="tool",
            content=json.dumps(
                {
                    "job_id": "job-1",
                    "file_name": "report.pdf",
                    "source_format": "pdf",
                    "target_format": "docx",
                }
            ),
            tool_call_id="c1",
            name="start_conversion",
        ),
    ]
    reply = _reply(messages)
    assert "report.pdf" in reply
    assert "docx" in reply


def test_a_tool_error_is_quoted_back_to_the_user() -> None:
    messages = [
        _user("convert report.pdf to docx"),
        LlmMessage(
            role="tool",
            content=json.dumps({"error": "that conversion is not supported"}),
            tool_call_id="c1",
            name="start_conversion",
        ),
    ]
    assert "that conversion is not supported" in _reply(messages)


def test_a_summary_result_is_rendered_with_its_key_points() -> None:
    messages = [
        _user("summarize report.pdf"),
        LlmMessage(
            role="tool",
            content=json.dumps({"summary": "It is a report.", "key_points": ["One", "Two"]}),
            tool_call_id="c1",
            name="summarize_file",
        ),
    ]
    reply = _reply(messages)
    assert "It is a report." in reply
    assert "- One" in reply


def test_a_supported_formats_result_is_rendered_as_a_list() -> None:
    messages = [
        _user("what can I convert a png to?"),
        LlmMessage(
            role="tool",
            content=json.dumps({"source_format": "png", "targets": ["jpg", "pdf"]}),
            tool_call_id="c1",
            name="list_supported_targets",
        ),
    ]
    reply = _reply(messages)
    assert "`jpg`" in reply
    assert "`pdf`" in reply


def test_an_unknown_tool_name_still_produces_an_answer() -> None:
    messages = [
        _user("hello"),
        LlmMessage(role="tool", content="{}", tool_call_id="c1", name="mystery_tool"),
    ]
    assert _reply(messages)


def test_the_stream_ends_with_a_done_chunk() -> None:
    chunks = _stream([_user("hello")])
    assert chunks[-1].kind == "done"
    assert chunks[-1].response is not None
    assert chunks[-1].response.content == "".join(
        chunk.text for chunk in chunks if chunk.kind == "text"
    )


def test_a_tool_request_streams_no_text_and_one_done_chunk() -> None:
    chunks = _stream([_user("what files do I have?")])
    assert [chunk.kind for chunk in chunks] == ["done"]
    assert chunks[0].response is not None
    assert chunks[0].response.tool_calls[0].name == "list_files"
