"""The "Transform AI" assistant API.

Two transports live side by side here, deliberately:

* ``POST /chat`` is Server-Sent Events — the model streams tokens and tool steps
  over seconds, and a buffered JSON response would make the UI look frozen for
  the whole turn. The frame vocabulary (``status``, ``delta``, ``tool``,
  ``artifact``, ``done``, ``error``) is the entire contract with the SPA.
* Everything else is an ordinary request/response endpoint.

The access checks happen *before* the SSE response starts, not inside it. A 403
(no allowance on this plan) and a 429 (hourly quota spent) are real HTTP
statuses here, because a client that has to parse an error out of a stream that
already returned 200 cannot show the right message — so the first event of the
service generator is pulled eagerly and any refusal it raises becomes the
response itself.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from src.application.dtos.assistant_dto import (
    Artifact,
    AssistantDone,
    AssistantError,
    AssistantEvent,
    AssistantTextDelta,
    AssistantToolEvent,
)
from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.ports.assistant_model_port import AssistantModelResolver
from src.application.ports.assistant_repository_port import AssistantQuotaPort
from src.application.services.assistant_service import (
    AssistantConversationNotFound,
    AssistantService,
)
from src.domain.assistant.entities.conversation import Conversation
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantAttachmentLimitExceeded,
    AssistantAttachmentNotFound,
    AssistantDeletionNotFound,
    AssistantDisabledError,
    AssistantQuotaExceeded,
    AssistantToolError,
)
from src.domain.assistant.policies.assistant_policy import (
    can_use_assistant,
    hourly_quota,
    max_actions_per_turn,
    max_attachments_for_tier,
    max_document_bytes_for_tier,
    model_label_for_tier,
    model_level_for_tier,
)
from src.infrastructure.adapters.repository.sql_conversation_repo import (
    SQLConversationRepository,
)
from src.infrastructure.adapters.repository.sql_subscription_repo import (
    SQLSubscriptionRepository,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.logging.audit import log_data_access
from src.presentation.api.dependencies.auth_dependencies import CurrentUser, RequestOrigin
from src.presentation.api.dependencies.service_dependencies import (
    get_assistant_model_registry,
    get_assistant_quota,
    get_assistant_service,
    get_conversation_repository,
    get_subscription_repository,
)
from src.presentation.schemas.assistant import (
    AssistantStatusResponse,
    ChatRequest,
    ConversationCreatedResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    DeletionOutcomeResponse,
    MessageResponse,
    RecommendationItemResponse,
    RecommendationResponse,
    RecommendRequest,
    ResolveDeletionRequest,
    SummarizeRequest,
    SummaryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

#: Idle seconds before a comment frame is sent. Proxies (and some browsers) drop
#: a connection that has been silent for a while, and a model's first token can
#: take longer than that on a long prompt.
_HEARTBEAT_SECONDS = 15

#: Cap on the history returned by ``GET /conversations/{id}``. The UI renders a
#: finite scrollback; an unbounded read would grow without limit.
_CONVERSATION_HISTORY_LIMIT = 200


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "type": artifact.type,
        "id": artifact.id,
        "name": artifact.name,
        "meta": artifact.meta,
    }


def _frames_for(event: AssistantEvent) -> Iterator[str]:
    """Translate one service event into its SSE frame(s).

    ``tool`` events also emit an ``artifact`` frame per artifact, so a client
    that only tracks artifacts does not have to dig inside tool payloads.
    """
    if isinstance(event, AssistantTextDelta):
        yield _frame("delta", {"text": event.text})
    elif isinstance(event, AssistantToolEvent):
        payload: dict[str, Any] = {
            "name": event.name,
            "label": event.label,
            "status": event.status,
        }
        if event.summary:
            payload["summary"] = event.summary
        if event.artifacts:
            payload["artifacts"] = [_artifact_payload(item) for item in event.artifacts]
        yield _frame("tool", payload)
        for artifact in event.artifacts:
            yield _frame("artifact", _artifact_payload(artifact))
    elif isinstance(event, AssistantDone):
        yield _frame(
            "done",
            {
                "conversation_id": event.conversation_id,
                "message_id": event.message_id,
                "user_message_id": event.user_message_id,
                "content": event.content,
                "artifacts": [_artifact_payload(item) for item in event.artifacts],
            },
        )
    elif isinstance(event, AssistantError):
        yield _frame("error", {"code": event.code, "message": event.message})


def _error_frame(exc: BaseException) -> str:
    """Map an exception that escaped the stream into an error frame.

    ``BaseException`` rather than ``Exception`` so the annotation covers
    everything the pump could have captured; only ``Exception`` instances are
    ever stored in it.
    """
    if isinstance(exc, AssistantConversationNotFound):
        return _frame("error", {"code": "NOT_FOUND", "message": str(exc)})
    if isinstance(exc, AssistantDisabledError):
        return _frame("error", {"code": "AI_NOT_AVAILABLE_FOR_TIER", "message": str(exc)})
    if isinstance(exc, AssistantQuotaExceeded):
        return _frame("error", {"code": "QUOTA_EXCEEDED", "message": str(exc)})
    logger.error("Assistant stream failed", exc_info=exc)
    return _frame(
        "error",
        {"code": "INTERNAL_ERROR", "message": "The assistant hit an unexpected error. Please try again."},
    )


def _document_audit(user_id: int, event: AssistantEvent) -> None:
    """Record the completed turn in the security audit log.

    Called for every event, and only a ``done`` event completes a turn, so an
    aborted stream leaves no false "the user chatted" record. The conversation
    id is the evidence trail; the message text is deliberately not logged.
    """
    if isinstance(event, AssistantDone):
        log_data_access(
            user_id=str(user_id),
            action="chat",
            resource="ai_conversation",
            conversation_id=event.conversation_id,
        )


async def _stream_frames(
    first: AssistantEvent,
    generator: AsyncIterator[AssistantEvent],
    request: Request,
    user_id: int,
) -> AsyncGenerator[str, None]:
    """Turn the service's event stream into SSE frames, with heartbeats.

    The generator is drained by a background task and read through a queue, so
    that a slow model does not block the heartbeat: ``asyncio.wait_for`` around
    ``__anext__`` would *cancel* the generator on every idle timeout and kill the
    response, whereas a timed-out ``queue.get()`` is harmless.
    """
    _document_audit(user_id, first)
    yield _frame("status", {"stage": "thinking"})
    for frame in _frames_for(first):
        yield frame

    queue: asyncio.Queue[AssistantEvent | None] = asyncio.Queue()
    failure: dict[str, Exception] = {}

    async def _pump() -> None:
        try:
            async for event in generator:
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 — surfaced to the consumer below
            failure["error"] = exc
        finally:
            await queue.put(None)

    task = asyncio.create_task(_pump())
    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                error = failure.get("error")
                if error is not None:
                    yield _error_frame(error)
                return
            _document_audit(user_id, event)
            for frame in _frames_for(event):
                yield frame
    finally:
        # Cancelling the pump propagates into the service generator, which is
        # how a disconnected client stops the work in flight.
        task.cancel()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=AssistantStatusResponse)
async def get_status(
    current_user: CurrentUser,
    subscriptions: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    models: Annotated[AssistantModelResolver, Depends(get_assistant_model_registry)],
    quota: Annotated[AssistantQuotaPort, Depends(get_assistant_quota)],
) -> AssistantStatusResponse:
    """Report the caller's access, entitlements and current usage.

    Requires auth (like every other assistant endpoint) so a caller cannot use
    it to learn how the deployment is configured without an account.

    Every allowance is read from the domain policy (``assistant_policy``) and
    the model from the registry, so this endpoint is a *view* of what is
    enforced — it can never advertise a number the server does not apply. The
    usage numbers come from ``quota.peek`` (best-effort, fails open to 0) so a
    Redis hiccup cannot take the status page down.
    """
    tier = await subscriptions.get_tier_for_user(current_user.id)
    requests_per_hour = hourly_quota(tier)
    used = max(0, await quota.peek(current_user.id))
    return AssistantStatusResponse(
        enabled=can_use_assistant(tier),
        backend=get_settings()._resolve_ai_backend(),
        model=models.for_tier(tier).model,
        tier=tier.value,
        model_level=model_level_for_tier(tier),
        model_label=model_label_for_tier(tier),
        requests_per_hour=requests_per_hour,
        used_this_hour=used,
        remaining_this_hour=max(0, requests_per_hour - used),
        max_attachments=max_attachments_for_tier(tier),
        max_actions_per_turn=max_actions_per_turn(tier),
        max_document_bytes=max_document_bytes_for_tier(tier),
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    current_user: CurrentUser,
    origin: RequestOrigin,
    assistant: Annotated[AssistantService, Depends(get_assistant_service)],
    subscriptions: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> StreamingResponse:
    """Stream one assistant turn as Server-Sent Events."""
    tier = await subscriptions.get_tier_for_user(current_user.id)
    generator = assistant.stream_chat(
        user_id=current_user.id,
        tier=tier,
        message=payload.message,
        conversation_id=payload.conversation_id,
        file_ids=payload.file_ids,
        context=payload.context,
        # How this request authenticated (API key vs browser). A conversion the
        # assistant starts is labelled with it so the worker picks the right
        # credit spend order instead of always assuming WEB.
        origin=origin,
    )

    # Pull the first event eagerly: the access checks (quota, tier, conversation
    # ownership, attachment ownership) run before the first yield, so this is
    # what turns them into real HTTP statuses instead of an error frame inside a
    # 200 stream.
    try:
        first = await anext(generator)
    except StopAsyncIteration as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The assistant produced no response.",
        ) from exc
    except AssistantAttachmentNotFound as exc:
        # An unknown id and a foreign one answer identically, so attachment ids
        # stay unenumerable (and the client learns its message was not processed
        # with the file it sent).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ATTACHMENT_NOT_FOUND", "message": str(exc)},
        ) from exc
    except AssistantConversationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        ) from exc
    except AssistantDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AI_NOT_AVAILABLE_FOR_TIER", "message": str(exc)},
        ) from exc
    except AssistantAttachmentLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ATTACHMENT_LIMIT_EXCEEDED", "message": str(exc)},
        ) from exc
    except AssistantQuotaExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "QUOTA_EXCEEDED", "message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("Assistant chat failed before streaming")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTERNAL_ERROR", "message": "The assistant is unavailable."},
        ) from exc

    return StreamingResponse(
        _stream_frames(first, generator, request, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def _conversation_response(row: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    current_user: CurrentUser,
    repository: Annotated[SQLConversationRepository, Depends(get_conversation_repository)],
) -> ConversationListResponse:
    """List the caller's conversations, most recently active first."""
    rows = await repository.list_conversations(current_user.id, limit=50)
    return ConversationListResponse(
        conversations=[_conversation_response(row) for row in rows]
    )


@router.post(
    "/conversations",
    response_model=ConversationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: CurrentUser,
    repository: Annotated[SQLConversationRepository, Depends(get_conversation_repository)],
) -> ConversationCreatedResponse:
    """Create an empty conversation (used by the "New chat" action)."""
    title = (payload.title or "New chat").strip() or "New chat"
    conversation = await repository.create_conversation(
        user_id=current_user.id, title=title
    )
    return ConversationCreatedResponse(
        id=conversation.id, title=conversation.title, created_at=conversation.created_at
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversationRepository, Depends(get_conversation_repository)],
) -> ConversationDetailResponse:
    """Return one conversation and its transcript.

    A conversation that does not exist and one owned by somebody else both
    answer 404, so ids stay unenumerable.
    """
    conversation = await repository.get_conversation(conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    messages = await repository.list_messages(
        conversation.id, limit=_CONVERSATION_HISTORY_LIMIT
    )
    log_data_access(
        user_id=str(current_user.id),
        action="read",
        resource="ai_conversation",
        conversation_id=conversation.id,
    )
    return ConversationDetailResponse(
        conversation=_conversation_response(conversation),
        messages=[
            MessageResponse(
                id=message.id,
                role=str(message.role),
                content=message.content,
                tool_name=message.tool_name,
                meta=message.meta,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversationRepository, Depends(get_conversation_repository)],
) -> Response:
    """Delete an owned conversation and its transcript."""
    if not await repository.delete_conversation(conversation_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    log_data_access(
        user_id=str(current_user.id),
        action="delete",
        resource="ai_conversation",
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def truncate_conversation_from_message(
    conversation_id: str,
    message_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversationRepository, Depends(get_conversation_repository)],
) -> Response:
    """Delete a message and every message after it ("edit and resend").

    The path has one more segment than ``/conversations/{conversation_id}``, so
    it cannot shadow (or be shadowed by) the literal-less conversation routes.

    Ownership is checked first, exactly like the other conversation endpoints;
    then a message id that is unknown or belongs to another conversation answers
    404 "Message not found", indistinguishable from each other, so ids stay
    unenumerable.
    """
    if await repository.get_conversation(conversation_id, current_user.id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    if not await repository.truncate_from_message(conversation_id, message_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    log_data_access(
        user_id=str(current_user.id),
        action="delete",
        resource="ai_conversation_message",
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Deletion confirmations
# ---------------------------------------------------------------------------


@router.post(
    "/conversations/{conversation_id}/deletions",
    response_model=DeletionOutcomeResponse,
)
async def resolve_deletion(
    conversation_id: str,
    payload: ResolveDeletionRequest,
    current_user: CurrentUser,
    assistant: Annotated[AssistantService, Depends(get_assistant_service)],
) -> DeletionOutcomeResponse:
    """Confirm or cancel an AI-proposed deletion of one of the caller's files.

    This is the only path that can actually delete a file on the assistant's
    behalf, and it is reachable *only* by an authenticated click — the model
    itself has no tool that reaches the deletion. The service requires a live
    ``pending`` proposal in the caller's own conversation, so this cannot be
    used as a blind "delete any file I own by id" endpoint and a second click
    on the same prompt resolves to a 404 instead of deleting twice.

    Deliberately NOT gated on the assistant tier or hourly quota: this is the
    user acting on their own file, the same authority the existing
    ``DELETE /api/v1/files/{id}`` already grants. Charging an assistant quota
    for a confirmation click — or refusing a click because the plan changed
    after the proposal was made — would be wrong.
    """
    try:
        outcome = await assistant.resolve_deletion(
            user_id=current_user.id,
            conversation_id=conversation_id,
            file_id=payload.file_id,
            approve=payload.approve,
        )
    except (AssistantConversationNotFound, AssistantDeletionNotFound) as exc:
        # One 404 for "no such conversation", "not your conversation" and "no
        # live proposal for that file": telling them apart would let a caller
        # enumerate conversation ids and probe which files were ever proposed
        # for deletion. The SPA only needs to know the prompt is stale.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DELETION_NOT_FOUND", "message": str(exc)},
        ) from exc
    log_data_access(
        user_id=str(current_user.id),
        action="delete" if payload.approve else "cancel",
        resource="ai_file_deletion",
        conversation_id=conversation_id,
        file_id=outcome.file_id,
        state=outcome.state,
    )
    return DeletionOutcomeResponse(
        file_id=outcome.file_id,
        file_name=outcome.file_name,
        state=outcome.state,
    )


# ---------------------------------------------------------------------------
# Summarise / recommend
# ---------------------------------------------------------------------------


@router.post("/summarize", response_model=SummaryResponse)
async def summarize(
    payload: SummarizeRequest,
    current_user: CurrentUser,
    assistant: Annotated[AssistantService, Depends(get_assistant_service)],
    subscriptions: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> SummaryResponse:
    """Summarise one of the caller's documents."""
    tier = await subscriptions.get_tier_for_user(current_user.id)
    try:
        result = await assistant.summarize_file(
            user_id=current_user.id, tier=tier, file_id=payload.file_id
        )
    except FileSystemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
    except AssistantDisabledError as exc:
        # Same codes as /chat so the SPA's existing copy handles it: a plan with
        # no assistant, or an hourly allowance already spent.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AI_NOT_AVAILABLE_FOR_TIER", "message": str(exc)},
        ) from exc
    except AssistantQuotaExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "QUOTA_EXCEEDED", "message": str(exc)},
        ) from exc
    except AssistantToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "DOCUMENT_NOT_READABLE", "message": str(exc)},
        ) from exc
    log_data_access(
        user_id=str(current_user.id),
        action="summarize",
        resource="ai_file_summary",
        file_id=result.file_id,
    )
    return SummaryResponse(
        file_id=result.file_id,
        file_name=result.file_name,
        summary=result.summary,
        key_points=result.key_points,
        model=result.model,
    )


@router.post("/recommend", response_model=RecommendationResponse)
async def recommend(
    payload: RecommendRequest,
    current_user: CurrentUser,
    assistant: Annotated[AssistantService, Depends(get_assistant_service)],
    subscriptions: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> RecommendationResponse:
    """Recommend target formats for a file, or for a bare source format."""
    if not payload.file_id and not payload.source_format:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "MISSING_SOURCE",
                "message": "Provide either file_id or source_format.",
            },
        )
    tier = await subscriptions.get_tier_for_user(current_user.id)
    try:
        result = await assistant.recommend(
            user_id=current_user.id,
            tier=tier,
            file_id=payload.file_id,
            source_format=payload.source_format,
            use_case=payload.use_case,
        )
    except FileSystemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
    except AssistantDisabledError as exc:
        # Same codes as /chat, so the SPA's existing handling applies.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AI_NOT_AVAILABLE_FOR_TIER", "message": str(exc)},
        ) from exc
    except AssistantQuotaExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "QUOTA_EXCEEDED", "message": str(exc)},
        ) from exc
    except AssistantToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_RECOMMENDATION", "message": str(exc)},
        ) from exc
    log_data_access(
        user_id=str(current_user.id),
        action="recommend",
        resource="ai_recommendation",
        source_format=result.source_format,
    )
    return RecommendationResponse(
        source_format=result.source_format,
        use_case=result.use_case,
        recommendations=[
            RecommendationItemResponse(
                target_format=item.target_format,
                label=item.label,
                category=item.category,
                reason=item.reason,
                confidence=item.confidence,
            )
            for item in result.recommendations
        ],
    )
