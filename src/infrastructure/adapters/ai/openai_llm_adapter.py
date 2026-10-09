"""OpenAI-compatible chat-completions transport for the AI assistant.

Talks to ``POST {AI_BASE_URL}/chat/completions`` with httpx instead of the
``openai`` SDK. WHY: the wire format is a documented JSON contract that every
serious provider implements, so speaking it directly buys compatibility with
OpenAI, Azure OpenAI, OpenRouter, Groq, Google Gemini (via its OpenAI
compatibility endpoint), vLLM, Ollama and LM Studio at once — while an SDK would
add a dependency, and pin this deployment to one vendor's release cadence, for
one POST request.

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

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Any

import httpx

from src.application.ports.llm_port import (
    LlmMessage,
    LlmResponse,
    LlmStreamChunk,
    LlmToolCall,
    LlmToolSpec,
    LlmUnavailableError,
)

logger = logging.getLogger(__name__)

#: A provider error body can be large and may echo request context; only a short
#: prefix is kept for the error message, and never the API key.
_MAX_ERROR_BODY_CHARS = 300

#: How many times one completion is attempted in total (so 2 retries). Small on
#: purpose: this sits inside a streaming request the user is waiting on, and a
#: long retry chain is indistinguishable from a hang.
_MAX_ATTEMPTS = 3

#: Backoff between attempts, used when the provider sends no ``Retry-After``.
#: The last value repeats if more attempts are ever configured.
_RETRY_BACKOFF_SECONDS = (2.0, 8.0)

#: Upper bound on the wait before ONE attempt, including a provider-supplied
#: ``Retry-After``. A provider is entitled to say "come back in an hour"; a
#: request worker is not entitled to wait for it.
_MAX_RETRY_WAIT_SECONDS = 15.0

#: Upper bound on time spent sleeping across ALL attempts of one completion.
#: Bounds the worst case a user can be made to wait before the error surfaces,
#: which matters because this is a synchronous streaming request.
_TOTAL_RETRY_BUDGET_SECONDS = 20.0


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
    ) -> AsyncGenerator[LlmStreamChunk]:
        """Stream one completion, emitting text deltas then the assembled result.

        A *transient* provider failure is retried, because the common one is
        throttling: a document-reading turn re-sends the whole prompt on every
        tool iteration, so it can cross a tokens-per-minute budget that a
        single request would have fitted inside. The provider usually says how
        long to wait, and waiting is far better than failing a conversion the
        user just asked for.

        Retrying is only safe because it happens strictly *before the first
        yield* — the status is checked as soon as the response opens, so a
        failed attempt has emitted nothing and a repeat cannot duplicate text
        into the caller's stream. Once the body starts streaming, a mid-stream
        failure is NOT retried: by then the caller has already seen deltas, and
        replaying the turn would append a second answer to the first.
        """
        payload = self._build_payload(messages, tools)
        slept = 0.0

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            async with self._client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
                if response.status_code == 200:
                    async for chunk in self._read_stream(response):
                        yield chunk
                    return

                body = (await response.aread()).decode("utf-8", errors="replace")
                message = (
                    f"Assistant model request failed with HTTP {response.status_code}: "
                    f"{body[:_MAX_ERROR_BODY_CHARS]}"
                )
                if not _is_retryable_status(response.status_code):
                    # A request we built wrong will be wrong again.
                    raise LlmRequestError(message)

                delay = _retry_delay_seconds(response, attempt)
                is_last = attempt == _MAX_ATTEMPTS
                if is_last or slept + delay > _TOTAL_RETRY_BUDGET_SECONDS:
                    # Out of attempts, or the next wait would exceed the total
                    # budget. Surface it as *transient* — the caller did nothing
                    # wrong, and the honest answer is "busy, try again" rather
                    # than an internal error.
                    logger.warning(
                        "Assistant model still failing after %d attempt(s) (HTTP %s, "
                        "waited %.1fs)",
                        attempt,
                        response.status_code,
                        slept,
                    )
                    raise LlmUnavailableError(message) from None

                logger.warning(
                    "Assistant model returned HTTP %s; retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    delay,
                    attempt,
                    _MAX_ATTEMPTS,
                )

            # Outside the response context: the failed connection is released
            # before we sleep, so a retry does not hold it open.
            await asyncio.sleep(delay)
            slept += delay

        # Unreachable: the loop either returns or raises on its final attempt.
        # Present so the function is total and the failure is explicit if the
        # attempt accounting is ever changed.
        raise LlmUnavailableError(
            "Assistant model request failed after all retries"
        ) from None

    async def _read_stream(self, response: httpx.Response) -> AsyncGenerator[LlmStreamChunk]:
        """Decode one successful SSE response into chunks."""
        content = ""
        finish_reason: str | None = None
        # index -> partial tool call. Fragments for one call can be split across
        # any number of chunks, so they are merged by index as they arrive.
        partials: dict[int, dict[str, str]] = {}

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


def _is_retryable_status(status_code: int) -> bool:
    """Whether repeating the same request has any chance of succeeding.

    429 is the provider throttling this deployment and 5xx is the provider
    briefly unhealthy — both are properties of the *moment*, so the identical
    request can succeed shortly. Every other 4xx (a malformed request, a
    rejected key, an unknown model) is a property of the request itself, and
    repeating it only multiplies the failure and the latency.
    """
    return status_code == 429 or status_code >= 500


def _parse_retry_after(raw: str | None) -> float | None:
    """``Retry-After`` in seconds, or None when absent/unusable.

    Only the delay-seconds form is read. The HTTP-date form is legal but no
    OpenAI-compatible provider is known to send it, and acting on a
    clock-skewed date would be worse than falling back to our own backoff.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """How long to wait before the next attempt.

    The provider's own ``Retry-After`` wins when it sends one — it knows when
    its accounting window resets, and guessing shorter just burns an attempt.
    Otherwise our own bounded exponential schedule is used.
    """
    retry_after = _parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return min(retry_after, _MAX_RETRY_WAIT_SECONDS)
    index = min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
    return _RETRY_BACKOFF_SECONDS[index]


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
        entry = partials.setdefault(
            index, {"id": "", "name": "", "arguments": "", "thought_signature": ""}
        )
        call_id = raw.get("id")
        if isinstance(call_id, str) and call_id:
            entry["id"] = call_id
        # Google's Gemini models attach a thought signature to each tool call
        # (under ``extra_content.google``); it must be echoed back verbatim on
        # the next request or the follow-up turn is rejected with a 400. It is
        # captured here and re-emitted by ``_to_wire_message``. Other
        # providers never send it, so this is a no-op for them.
        extra = raw.get("extra_content")
        if isinstance(extra, dict):
            google = extra.get("google")
            if isinstance(google, dict):
                signature = google.get("thought_signature")
                if isinstance(signature, str) and signature:
                    entry["thought_signature"] = signature
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
        thought_signature=partial.get("thought_signature") or None,
    )


def _tool_call_to_wire(call: LlmToolCall) -> dict[str, Any]:
    """Render one tool call for an assistant turn.

    ``extra_content.google.thought_signature`` is re-emitted only when the call
    carries one, so providers that do not use thought signatures (OpenAI, Groq)
    see the exact payload they always did. Gemini requires it on every replayed
    function call and rejects the request without it.
    """
    entry: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments),
        },
    }
    if call.thought_signature:
        entry["extra_content"] = {
            "google": {"thought_signature": call.thought_signature}
        }
    return entry


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
            "tool_calls": [_tool_call_to_wire(call) for call in message.tool_calls],
        }
    if message.role == "tool":
        wire: dict[str, Any] = {"role": "tool", "content": message.content}
        if message.tool_call_id is not None:
            wire["tool_call_id"] = message.tool_call_id
        if message.name:
            wire["name"] = message.name
        return wire
    return {"role": message.role, "content": message.content}
