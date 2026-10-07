"""Request/response schemas for the AI assistant API.

The response models mirror the DTOs the service returns. They exist as separate
pydantic models rather than as the dataclasses themselves because the HTTP
contract and the internal value objects have different reasons to change — and a
field rename in either should not silently break the other.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssistantStatusResponse(BaseModel):
    """Whether the caller can use the assistant, and what is behind it.

    The first three fields are the original contract. Everything below them was
    added later and is defaulted on purpose: an older client that reads only
    ``enabled``/``backend``/``model`` sees no change, while a newer one can
    render the caller's plan, the model *level* (never the raw model id) and the
    exact allowances the server enforces — all sourced from the domain policy,
    so the UI cannot show a limit the backend does not apply.
    """

    enabled: bool = Field(description="False when the caller's tier has no allowance")
    backend: str = Field(description="Resolved transport: 'openai', 'gemini' or 'echo'")
    model: str = Field(description="Model identifier, or 'echo' for the offline backend")

    tier: str = Field(default="", description="Caller's subscription tier")
    model_level: str = Field(default="", description="standard | advanced | priority")
    model_label: str = Field(default="", description="Human label for the model level")
    requests_per_hour: int = Field(default=0, description="Hourly assistant turn allowance")
    used_this_hour: int = Field(default=0, description="Turns used in the current hour")
    remaining_this_hour: int = Field(default=0, description="Turns left in the current hour")
    max_attachments: int = Field(default=0, description="Files allowed per message")
    max_actions_per_turn: int = Field(default=0, description="Conversions allowed per turn")
    max_document_bytes: int = Field(default=0, description="Largest readable document, in bytes")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation to continue; omit to start a new one",
    )
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Ids of owned files to attach to this message (max 5)",
    )
    context: str | None = Field(
        default=None,
        max_length=200,
        description="Untrusted hint about the page the user is viewing",
    )


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationCreatedResponse(BaseModel):
    id: str
    title: str
    created_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


class MessageResponse(BaseModel):
    """One transcript entry.

    ``meta`` is the stored tool-call bookkeeping. It is returned because the UI
    uses it to re-render past turns with their tool steps, and it contains no
    secrets — only tool names, ids and arguments the caller already saw.
    """

    id: str
    role: str
    content: str
    tool_name: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]


class ResolveDeletionRequest(BaseModel):
    """The user's decision on an AI-proposed deletion.

    Only the file id and the yes/no decision travel: the conversation the
    proposal belongs to is in the path, and everything else (name, size) is
    re-read server-side from the recorded proposal so a client cannot rename or
    invent a file in the request body.
    """

    file_id: str = Field(
        min_length=1,
        max_length=64,
        description="Id of the file whose deletion the assistant proposed",
    )
    approve: bool = Field(
        description="True to delete the file now; False to cancel the proposal",
    )


class DeletionOutcomeResponse(BaseModel):
    """The resolved outcome of a deletion confirmation.

    ``state`` is one of ``deleted`` / ``cancelled`` / ``failed``; ``failed``
    means the file was already gone by the time the user confirmed, which is a
    resolved outcome rather than an error.
    """

    file_id: str = Field(description="Id of the file the proposal was about")
    file_name: str = Field(description="Display name of the file at proposal time")
    state: str = Field(description="deleted | cancelled | failed")


class SummarizeRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)


class SummaryResponse(BaseModel):
    file_id: str
    file_name: str
    summary: str
    key_points: list[str]
    model: str


class RecommendRequest(BaseModel):
    """At least one of ``file_id`` / ``source_format`` is required.

    Enforced in the route (not with a pydantic model validator) so the error is
    the API's usual structured 422 body rather than a raw validation error.
    """

    file_id: str | None = Field(default=None, max_length=64)
    source_format: str | None = Field(default=None, max_length=20)
    use_case: str | None = Field(default=None, max_length=500)


class RecommendationItemResponse(BaseModel):
    target_format: str
    label: str
    category: str
    reason: str
    confidence: float


class RecommendationResponse(BaseModel):
    source_format: str
    use_case: str | None = None
    recommendations: list[RecommendationItemResponse]
