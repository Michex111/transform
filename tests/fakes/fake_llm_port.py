"""Scripted ``LlmPort`` double.

Tests need to drive the assistant's agent loop without a provider: a queued list
of responses makes the model's behaviour explicit and repeatable, and recording
every call lets a test assert on the prompt it was given. When the queue runs
out, a short "All done." answer is returned so a test that only cares about one
step still finishes the turn.
"""

from collections.abc import AsyncGenerator, Sequence

from src.application.ports.llm_port import (
    LlmMessage,
    LlmResponse,
    LlmStreamChunk,
    LlmToolCall,
    LlmToolSpec,
)


def text_response(content: str) -> LlmResponse:
    """A plain assistant answer."""
    return LlmResponse(content=content, tool_calls=())


def tool_response(name: str, arguments: dict | None = None, content: str = "") -> LlmResponse:
    """An assistant turn that asks for one tool call."""
    return LlmResponse(
        content=content,
        tool_calls=(
            LlmToolCall(id=f"call_{name}", name=name, arguments=arguments or {}),
        ),
        finish_reason="tool_calls",
    )


class FakeLlmPort:
    """Replays queued responses and records every call."""

    def __init__(
        self,
        responses: Sequence[LlmResponse] | None = None,
        model: str = "fake-model",
        chunk_size: int = 0,
    ) -> None:
        self._responses = list(responses or [])
        self.calls: list[tuple[tuple[LlmMessage, ...], tuple[LlmToolSpec, ...]]] = []
        self._model = model
        #: Split streamed text into pieces of this size (0 = one piece). Tests
        #: set it to prove the caller accumulates deltas correctly.
        self._chunk_size = chunk_size

    @property
    def model(self) -> str:
        return self._model

    @property
    def last_messages(self) -> tuple[LlmMessage, ...]:
        return self.calls[-1][0]

    async def stream(
        self,
        *,
        messages: Sequence[LlmMessage],
        tools: Sequence[LlmToolSpec],
    ) -> AsyncGenerator[LlmStreamChunk]:
        self.calls.append((tuple(messages), tuple(tools)))
        response = (
            self._responses.pop(0) if self._responses else text_response("All done.")
        )
        content = response.content
        if content:
            size = self._chunk_size or len(content)
            for start in range(0, len(content), size):
                yield LlmStreamChunk(kind="text", text=content[start : start + size])
        yield LlmStreamChunk(kind="done", response=response)
