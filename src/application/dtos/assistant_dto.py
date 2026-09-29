"""Transport-neutral DTOs for the AI assistant.

Two groups live here:

* **Stream events** (``AssistantTextDelta`` / ``AssistantToolEvent`` /
  ``AssistantDone`` / ``AssistantError``) — the union the service's
  ``stream_chat`` yields. The router turns each one into an SSE frame and never
  sees anything else, so the SSE vocabulary is defined exactly once, here.
* **Result DTOs** (``SummaryResult``, ``RecommendationResult``, ...) — the return
  shapes of the non-streaming endpoints, which are also the shapes the pydantic
  response models mirror.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias


@dataclass(frozen=True)
class Artifact:
    """A concrete thing an assistant turn produced or touched.

    The frontend renders these as clickable chips, so ``type`` is the minimum
    needed to route a click (``file`` opens the file page, ``job`` opens the job
    detail) and ``meta`` carries whatever that view needs. Kept as ``str`` rather
    than an enum so a new artifact kind does not require a coordinated release.
    """

    type: str
    id: str
    name: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssistantTextDelta:
    """Incremental assistant text to append to the message being rendered."""

    text: str


@dataclass(frozen=True)
class AssistantToolEvent:
    """A tool call became visible to the user.

    Emitted twice per call: once with ``status="running"`` before it executes
    (so the UI can show "Looking through your files…") and once with
    ``status="done"`` carrying the human ``summary`` and any ``artifacts``.
    """

    name: str
    label: str
    status: str
    summary: str = ""
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class AssistantDone:
    """The turn finished successfully; ``content`` is the full final text.

    ``message_id`` is the *assistant* reply; ``user_message_id`` is the user
    message that opened the turn. The client needs the latter to target a
    server-side truncate ("edit and resend") without reloading the transcript.
    """

    conversation_id: str
    message_id: str
    content: str
    artifacts: tuple[Artifact, ...] = ()
    user_message_id: str = ""


@dataclass(frozen=True)
class AssistantError:
    """The turn failed in a way worth showing the user.

    ``code`` is a stable machine-readable token (``QUOTA_EXCEEDED``,
    ``INTERNAL_ERROR``, ...); ``message`` is already user-appropriate.
    """

    code: str
    message: str


#: Everything ``AssistantService.stream_chat`` can yield.
AssistantEvent: TypeAlias = (
    AssistantTextDelta | AssistantToolEvent | AssistantDone | AssistantError
)


@dataclass(frozen=True)
class SummaryResult:
    """The outcome of ``POST /assistant/summarize``."""

    file_id: str
    file_name: str
    summary: str
    key_points: list[str]
    model: str


@dataclass(frozen=True)
class RecommendationItem:
    """One suggested target format."""

    target_format: str
    label: str
    category: str
    reason: str
    confidence: float


@dataclass(frozen=True)
class RecommendationResult:
    """The outcome of ``POST /assistant/recommend``."""

    source_format: str
    use_case: str | None
    recommendations: list[RecommendationItem]


@dataclass(frozen=True)
class ConversationSummaryDto:
    """A conversation as listed in the sidebar (no messages)."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
