"""Dependency-inversion port for the chat model that drives the AI assistant.

WHY this exists: the same assistant code has to run against three very
different things — a real OpenAI-compatible endpoint in production, a
deterministic offline rule engine in development/CI (see the ``echo`` backend),
and a scripted fake in tests. A vendor SDK type in the application layer would
make two of those impossible without an installed SDK, so the application
depends on this narrow port instead: describe the model, stream one completion.

The message/tool shapes are deliberately *ours* (not the provider's wire
format). The OpenAI wire format is a transport detail of one adapter, and
``tool``/``assistant+tool_calls`` messages are assembled by the caller from
stored history, so the port stays a stable description of "a chat turn".
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class LlmUnavailableError(RuntimeError):
    """The provider could not serve this request, but may serve a later one.

    Declared on the PORT rather than inside an adapter because the layer above
    has to tell "the provider is busy, waiting will help" apart from "this
    request is wrong, retrying never will" — and it must do that without
    importing a transport. Adapters raise this once their own retries are
    exhausted on a *transient* status (throttling, a brief 5xx); a permanent
    failure (a malformed request, a rejected key) is a bug in the request and
    is raised as the adapter's own hard-failure type instead.

    The distinction is what lets the presentation layer answer with "the
    assistant is busy, try again shortly" instead of a generic internal error —
    a materially different thing to tell a user who is waiting on a document.
    """


@dataclass(frozen=True)
class LlmToolCall:
    """One function call the model asked for.

    ``arguments`` is already-decoded JSON. Providers stream the arguments as a
    JSON *string* in fragments; decoding is the adapter's job so the
    application never has to reason about half-parsed JSON.

    ``thought_signature`` is an opaque, provider-issued token that must be
    replayed verbatim with the call on the next request. Google's Gemini models
    attach one to every function call and reject a follow-up turn that omits it
    ("Function call is missing a thought_signature"). It is meaningless to
    providers that do not use it, so it stays ``None``/absent there and the
    application simply carries the value back and forth without reading it.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None


@dataclass(frozen=True)
class LlmToolSpec:
    """The JSON-schema description of one tool advertised to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class LlmMessage:
    """One item of chat history.

    Exactly one of the role-specific payloads is meaningful: ``tool_calls`` for
    an assistant turn that requested tools, ``tool_call_id``/``name`` for the
    ``tool`` result answering one of those calls. ``content`` is the plain text
    in every case (and may be empty for a tool-calling assistant turn).
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[LlmToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class LlmResponse:
    """A complete assistant turn: text and/or the tools it wants to run."""

    content: str
    tool_calls: tuple[LlmToolCall, ...] = ()
    finish_reason: str | None = None


@dataclass(frozen=True)
class LlmStreamChunk:
    """One item of a streamed completion.

    ``kind="text"`` carries an incremental delta to append; ``kind="done"`` is
    sent exactly once, last, and carries the assembled response (including any
    tool calls, which only become usable once fully accumulated).
    """

    kind: Literal["text", "done"]
    text: str = ""
    response: LlmResponse | None = None


@runtime_checkable
class LlmPort(Protocol):
    """Streams chat completions, optionally with tool calling."""

    @property
    def model(self) -> str:
        """Identifier of the model in use (reported by ``GET /assistant/status``).

        Adapters for non-model backends answer with their own name (``"echo"``)
        rather than a fabricated model id, so the value can be shown to the
        user and trusted.
        """
        ...

    def stream(
        self,
        *,
        messages: Sequence[LlmMessage],
        tools: Sequence[LlmToolSpec],
    ) -> AsyncIterator[LlmStreamChunk]:
        """Stream one completion for ``messages`` with ``tools`` available.

        Declared as a plain ``def`` returning an async iterator (not ``async
        def``) because callers iterate it directly — there is no separate
        "start the request" await. Raises when the provider fails; the
        application maps that to an error frame, never to a 500 body.
        """
        ...
