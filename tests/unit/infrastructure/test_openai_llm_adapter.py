"""Unit tests for the OpenAI-compatible adapter, driven by httpx's mock transport.

The streaming protocol is the only genuinely fiddly part of the integration, and
it is where a silent bug is most expensive: tool-call fragments arrive split
across chunks, so a parser that reads ``function.arguments`` as a complete JSON
value per chunk works for short arguments and fails for real ones. These tests
feed a fragmented stream through the real adapter (no network, no monkeypatching
of the case under test) and assert on both the parsed result and the request
that produced it.
"""

import asyncio
import json
from collections.abc import Sequence

import httpx
import pytest

from src.application.ports.llm_port import LlmMessage, LlmToolCall, LlmToolSpec
from src.infrastructure.adapters.ai.openai_llm_adapter import (
    LlmRequestError,
    OpenAiLlmAdapter,
)


def _sse(*chunks: object) -> bytes:
    """Render an SSE body: objects become ``data: <json>`` lines, strings as-is."""
    lines = []
    for chunk in chunks:
        payload = chunk if isinstance(chunk, str) else json.dumps(chunk)
        lines.append(f"data: {payload}")
    return ("\n\n".join(lines) + "\n\n").encode()


def _delta(content: str) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}


def _adapter(
    handler, *, model: str = "gpt-test", max_tokens: int = 1200, temperature: float = 0.3
) -> OpenAiLlmAdapter:
    """An adapter wired to ``handler`` instead of the network.

    ``api_key``/``base_url`` are still passed (the constructor requires them for
    a real deployment) but the injected client — and therefore MockTransport —
    is what actually handles the request.
    """
    return OpenAiLlmAdapter(
        api_key="sk-test",
        base_url="https://api.example.test/v1",
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        client=httpx.AsyncClient(
            base_url="https://api.example.test/v1",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer sk-test"},
        ),
    )


def _stream(adapter: OpenAiLlmAdapter, messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec] = ()):
    async def _collect():
        chunks = []
        async for chunk in adapter.stream(messages=messages, tools=tools):
            chunks.append(chunk)
        return chunks

    return asyncio.run(_collect())


USER = [LlmMessage(role="user", content="hi")]
TOOL = LlmToolSpec(name="list_files", description="list", parameters={"type": "object"})


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_streams_text_deltas_then_a_done_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(_delta("Hel"), _delta("lo"), _delta(" there"), "[DONE]"),
        )

    chunks = _stream(_adapter(handler), USER)

    assert [chunk.kind for chunk in chunks] == ["text", "text", "text", "done"]
    assert "".join(chunk.text for chunk in chunks) == "Hello there"
    assert chunks[-1].response is not None
    assert chunks[-1].response.content == "Hello there"
    assert chunks[-1].response.tool_calls == ()


def test_assembles_a_tool_call_split_across_chunks() -> None:
    """The argument JSON arrives in fragments; only the last one completes it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"index": 0, "delta": {"content": "Looking"}, "finish_reason": None}]},
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "list_files", "arguments": '{"que'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'ry": "rep'}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": 'ort"}'}}]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                "[DONE]",
            ),
        )

    chunks = _stream(_adapter(handler), USER, [TOOL])
    response = chunks[-1].response

    assert response is not None
    assert response.content == "Looking"
    assert response.tool_calls == (
        LlmToolCall(id="call_1", name="list_files", arguments={"query": "report"}),
    )
    assert response.finish_reason == "tool_calls"


def test_keeps_two_tool_calls_separate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        fragment = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{}"}},
                            {"index": 1, "id": "b", "function": {"name": "two", "arguments": "{}"}},
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
        return httpx.Response(200, content=_sse(fragment, "[DONE]"))

    response = _stream(_adapter(handler), USER, [TOOL])[-1].response
    assert response is not None
    assert [(call.id, call.name) for call in response.tool_calls] == [("a", "one"), ("b", "two")]


def test_ignores_keepalives_and_malformed_lines() -> None:
    """A proxy injecting noise must not kill an otherwise healthy stream."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = b": keep-alive\n\ndata: not json\n\n" + _sse(_delta("ok"), "[DONE]")
        return httpx.Response(200, content=body)

    chunks = _stream(_adapter(handler), USER)
    assert "".join(chunk.text for chunk in chunks) == "ok"


def test_undecodable_tool_arguments_degrade_to_empty() -> None:
    """The tool then reports a missing argument — a sentence the model can fix."""

    def handler(request: httpx.Request) -> httpx.Response:
        fragment = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{oops"}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
        return httpx.Response(200, content=_sse(fragment, "[DONE]"))

    response = _stream(_adapter(handler), USER, [TOOL])[-1].response
    assert response is not None
    assert response.tool_calls[0].arguments == {}


def test_a_non_200_response_raises_with_the_status_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key"}})

    with pytest.raises(LlmRequestError, match="401"):
        _stream(_adapter(handler), USER)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_request_payload_carries_the_model_tools_and_stream_flag() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    adapter = _adapter(handler, max_tokens=42, temperature=0.5)
    _stream(adapter, USER, [TOOL])

    assert captured["model"] == "gpt-test"
    assert captured["stream"] is True
    assert captured["max_tokens"] == 42
    assert captured["temperature"] == 0.5
    assert captured["tool_choice"] == "auto"
    assert captured["tools"][0]["function"]["name"] == "list_files"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


def test_request_omits_tool_choice_when_no_tools_are_offered() -> None:
    """An OpenAI-compatible server rejects ``tool_choice`` without a ``tools``
    array, which is why the summarise/recommend calls must not send it."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    _stream(_adapter(handler), USER)

    assert "tools" not in captured
    assert "tool_choice" not in captured


def test_history_is_mapped_to_the_wire_format() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    messages = [
        LlmMessage(role="system", content="sys"),
        LlmMessage(role="user", content="run it"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=(LlmToolCall(id="c1", name="list_files", arguments={"query": "x"}),),
        ),
        LlmMessage(role="tool", content='{"count": 1}', tool_call_id="c1", name="list_files"),
    ]
    _stream(_adapter(handler), messages, [TOOL])

    assistant, tool = captured["messages"][2], captured["messages"][3]
    # The provider requires the arguments as a JSON *string*, and the tool result
    # to name the call it answers — squashing either breaks the next request.
    assert assistant["tool_calls"][0] == {
        "id": "c1",
        "type": "function",
        "function": {"name": "list_files", "arguments": '{"query": "x"}'},
    }
    assert tool == {"role": "tool", "content": '{"count": 1}', "tool_call_id": "c1", "name": "list_files"}


def test_model_property_reports_the_configured_model() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, content=b""), model="gpt-4o")
    assert adapter.model == "gpt-4o"


def test_the_client_is_closable() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, content=b""))
    asyncio.run(adapter.aclose())
