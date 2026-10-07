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

from src.application.ports.llm_port import (
    LlmMessage,
    LlmToolCall,
    LlmToolSpec,
    LlmUnavailableError,
)
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


def test_a_gemini_thought_signature_is_captured_from_the_stream() -> None:
    """Google attaches a signature to each tool call; it must survive decoding.

    Without it, the follow-up turn is rejected with a 400 ("Function call is
    missing a thought_signature"), so dropping it here breaks every Gemini
    tool-using conversation.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        fragment = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "extra_content": {
                                    "google": {"thought_signature": "c2lnbmF0dXJl"}
                                },
                                "function": {"name": "list_files", "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        return httpx.Response(200, content=_sse(fragment, "[DONE]"))

    response = _stream(_adapter(handler), USER, [TOOL])[-1].response
    assert response is not None
    assert response.tool_calls[0].thought_signature == "c2lnbmF0dXJl"


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
# Transient failures (throttling and brief 5xx)
#
# The reported failure this exists for: a document-reading turn re-sends the
# whole prompt on every tool iteration, so it can cross a provider's
# tokens-per-minute budget that a single request would have fitted inside.
# Groq answers 429 with a `Retry-After`, and failing the turn outright when a
# few seconds of waiting would fix it is the wrong trade.
# ---------------------------------------------------------------------------


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the adapter's retry sleeps instead of performing them.

    Two reasons this is not a real ``sleep``: the suite must not take seconds
    per case, and the *duration* is part of the contract under test (a
    provider's ``Retry-After`` must be honoured, a wild one must be capped).
    """
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


def _throttle_then_ok(calls: dict[str, int], **throttle_kwargs: object):
    """A handler that throttles on the first call and streams on the second."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate"}}, **throttle_kwargs)  # type: ignore[arg-type]
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    return handler


def test_a_throttled_completion_is_retried_and_then_succeeds(slept: list[float]) -> None:
    calls = {"n": 0}

    chunks = _stream(_adapter(_throttle_then_ok(calls)), USER)

    assert calls["n"] == 2
    assert "".join(chunk.text for chunk in chunks) == "ok"
    assert chunks[-1].kind == "done"
    # Our own backoff, because this response sent no `Retry-After`.
    assert slept == [2.0]


def test_the_providers_retry_after_is_honoured(slept: list[float]) -> None:
    """The provider knows when its window resets; guessing shorter burns an attempt."""
    calls = {"n": 0}

    _stream(_adapter(_throttle_then_ok(calls, headers={"retry-after": "7"})), USER)

    assert slept == [7.0]


def test_a_server_error_is_retried_too(slept: list[float]) -> None:
    """A brief 5xx is a property of the moment, exactly like a 429."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    chunks = _stream(_adapter(handler), USER)

    assert calls["n"] == 2
    assert "".join(chunk.text for chunk in chunks) == "ok"


def test_a_permanent_client_error_is_never_retried(slept: list[float]) -> None:
    """A request we built wrong will be wrong again — retrying only adds latency."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    with pytest.raises(LlmRequestError, match="400"):
        _stream(_adapter(handler), USER)

    assert calls["n"] == 1
    assert slept == []


def test_exhausted_retries_surface_as_transient_not_a_hard_failure(
    slept: list[float],
) -> None:
    """Out of attempts is still "busy", not "broken".

    The caller did nothing wrong, so the presentation layer must be able to say
    "try again shortly" rather than "an unexpected error occurred".
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "rate"}})

    with pytest.raises(LlmUnavailableError):
        _stream(_adapter(handler), USER)

    assert calls["n"] == 3  # every attempt was used


def test_a_huge_retry_after_cannot_park_the_request(slept: list[float]) -> None:
    """A provider may say "come back in an hour"; a request worker may not wait."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429, json={"error": {"message": "rate"}}, headers={"retry-after": "3600"}
        )

    with pytest.raises(LlmUnavailableError):
        _stream(_adapter(handler), USER)

    assert max(slept) <= 15.0, slept
    assert sum(slept) <= 20.0, slept


def test_a_retry_emits_nothing_before_it_succeeds(slept: list[float]) -> None:
    """The retry must not append a second answer to a stream already read.

    Nothing is yielded until a successful response starts streaming, so a
    caller that reads this iterator sees exactly one turn — the reason the
    retry is confined to the pre-stream status check.
    """
    calls = {"n": 0}

    chunks = _stream(_adapter(_throttle_then_ok(calls)), USER)

    assert [chunk.kind for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "ok"


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


def test_a_thought_signature_is_replayed_on_the_wire() -> None:
    """A captured signature is echoed back on the assistant tool call."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    messages = [
        LlmMessage(role="user", content="run it"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=(
                LlmToolCall(
                    id="c1",
                    name="list_files",
                    arguments={"query": "x"},
                    thought_signature="c2lnbmF0dXJl",
                ),
            ),
        ),
    ]
    _stream(_adapter(handler), messages, [TOOL])

    assert captured["messages"][1]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "c2lnbmF0dXJl"}
    }


def test_a_tool_call_without_a_signature_carries_no_extra_content() -> None:
    """Non-Gemini providers must see the exact payload they always did."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    messages = [
        LlmMessage(role="user", content="run it"),
        LlmMessage(
            role="assistant",
            content="",
            tool_calls=(LlmToolCall(id="c1", name="list_files", arguments={}),),
        ),
    ]
    _stream(_adapter(handler), messages, [TOOL])

    assert "extra_content" not in captured["messages"][1]["tool_calls"][0]


def test_model_property_reports_the_configured_model() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, content=b""), model="gpt-4o")
    assert adapter.model == "gpt-4o"


def test_the_client_is_closable() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, content=b""))
    asyncio.run(adapter.aclose())
