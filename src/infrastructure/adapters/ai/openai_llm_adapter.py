"""OpenAI-compatible chat-completions transport for the AI assistant.

Talks to ``POST {AI_BASE_URL}/chat/completions`` with httpx instead of the
``openai`` SDK. WHY: the wire format is a documented JSON contract that every
serious provider implements, so speaking it directly buys compatibility with
OpenAI, Azure OpenAI, OpenRouter, Groq, vLLM, Ollama and LM Studio at once —
while an SDK would add a dependency, and pin this deployment to one vendor's
release cadence, for one POST request.

The only genuinely fiddly part is the streaming protocol, and it is handled
explicitly here:

* Responses are Server-Sent Events: ``data: {json}`` lines, terminated by
  ``data: [DONE]``. Blank lines and ``:`` comments are keep-alives.
* Text arrives as ``choices[0].delta.content`` fragments.
* Tool calls arrive as *fragments keyed by index*: the first fragment carries
  ``id`` and ``function.name``, and the (potentially many) later fragments
  append to ``function.arguments`` — which is itself a JSON string that is only
  parseable once the last fragment has arrived. Accumulating by index and
  decoding at the end is the only correct way to read it.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from src.application.ports.llm_port import (
    LlmMessage,
    LlmResponse,
    LlmStreamChunk,
    LlmToolCall,
    LlmToolSpec,
)

logger = logging.getLogger(__name__)

#: A provider error body can be large and may echo request context; only a short
#: prefix is kept for the error message, and never the API key.
_MAX_ERROR_BODY_CHARS = 300


class LlmRequestError(RuntimeError):
    """The provider refused or failed the completion request."""


class OpenAiLlmAdapter:
    """Streams completions from any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 1200,
        temperature: float = 0.3,
        timeout_seconds: int = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Injectable so tests can drive this class with httpx's MockTransport
        # instead of patching the network stack globally. When omitted, one
        # client (and one connection pool) is created and kept for the process.
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        """Close the underlying connection pool (used on shutdown/tests)."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_payload(
        self, messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_wire_message(message) for message in messages],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        if tools:
            # Sent only when there are tools. ``tool_choice`` without a ``tools``
            # array is rejected outright by OpenAI-compatible servers, and the
            # summarise/recommend calls deliberately pass no tools.
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"
        return payload

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(
        self,
        *,
        messages: Sequence[LlmMessage],
        tools: Sequence[LlmToolSpec],
    ) -> AsyncIterator[LlmStreamChunk]:
        """Stream one completion, emitting text deltas then the assembled result."""
        payload = self._build_payload(messages, tools)
        content = ""
        finish_reason: str | None = None
        # index -> partial tool call. Fragments for one call can be split across
        # any number of chunks, so they are merged by index as they arrive.
        partials: dict[int, dict[str, str]] = {}

        async with self._client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise LlmRequestError(
                    f"Assistant model request failed with HTTP {response.status_code}: "
                    f"{body[:_MAX_ERROR_BODY_CHARS]}"
                )

            async for line in response.aiter_lines():
                data = _sse_payload(line)
                if data is None:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk: Any = json.loads(data)
                except ValueError:
                    # A truncated or non-JSON keep-alive line: skip it rather
                    # than killing an otherwise healthy stream.
                    continue
                if not isinstance(chunk, dict):
                    continue

                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                if isinstance(choice.get("finish_reason"), str):
                    finish_reason = choice["finish_reason"]

                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                text = delta.get("content")
                if isinstance(text, str) and text:
                    content += text
                    yield LlmStreamChunk(kind="text", text=text)
                _accumulate_tool_calls(delta.get("tool_calls"), partials)

        response_obj = LlmResponse(
            content=content,
            tool_calls=tuple(
                _finalise_tool_call(index, partial) for index, partial in sorted(partials.items())
            ),
            finish_reason=finish_reason,
        )
        yield LlmStreamChunk(kind="done", response=response_obj)


def _sse_payload(line: str) -> str | None:
    """The payload of an SSE ``data:`` line, or ``None`` for noise.

    Keep-alive comments (``: ping``) and the blank line that ends every event
    carry no data, and some proxies inject extra whitespace, so both are
    tolerated.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.startswith("data:"):
        return None
    return stripped[len("data:") :].strip()


def _accumulate_tool_calls(
    raw_calls: Any, partials: dict[int, dict[str, str]]
) -> None:
    """Merge one chunk's ``delta.tool_calls`` fragments into ``partials``."""
    if not isinstance(raw_calls, list):
        return
    for position, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        index = raw.get("index")
        if not isinstance(index, int):
            index = position
        entry = partials.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = raw.get("id")
        if isinstance(call_id, str) and call_id:
            entry["id"] = call_id
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            entry["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            entry["arguments"] += arguments


def _finalise_tool_call(index: int, partial: dict[str, str]) -> LlmToolCall:
    """Turn accumulated fragments into a usable tool call.

    ``arguments`` is decoded once, at the end, because each fragment is a slice
    of a JSON string — decoding mid-stream would parse an incomplete document.
    An undecodable payload degrades to ``{}`` so the tool reports a missing
    argument (a sentence the model can act on) instead of the turn crashing.
    """
    raw_arguments = partial["arguments"].strip()
    arguments: dict[str, Any] = {}
    if raw_arguments:
        try:
            decoded: Any = json.loads(raw_arguments)
        except ValueError:
            logger.warning("Discarding undecodable tool arguments for index %s", index)
        else:
            if isinstance(decoded, dict):
                arguments = decoded
    return LlmToolCall(
        id=partial["id"] or f"call_{index}",
        name=partial["name"],
        arguments=arguments,
    )


def _to_wire_message(message: LlmMessage) -> dict[str, Any]:
    """Map our message shape onto the provider's wire format.

    The two provider-specific rules encoded here are the ones that make a
    multi-step tool conversation replayable at all: an assistant turn that
    requested tools must carry them (as JSON strings, one per call), and a tool
    result must carry the id of the call it answers.
    """
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ],
        }
    if message.role == "tool":
        wire: dict[str, Any] = {"role": "tool", "content": message.content}
        if message.tool_call_id is not None:
            wire["tool_call_id"] = message.tool_call_id
        if message.name:
            wire["name"] = message.name
        return wire
    return {"role": message.role, "content": message.content}
