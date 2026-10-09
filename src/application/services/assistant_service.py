"""Orchestrates an assistant turn: quota, transcript, model loop, tools.

WHY this is one object: a turn is a state machine over the provider's
request/response cycle — persist the user message, replay a bounded history,
stream a completion, run any tools it asked for, feed the results back, repeat
until it answers or the loop budget runs out. Every step has to agree with the
others about what is stored, so splitting it would spread one invariant
("the transcript and the prompt are the same list") across several files.

Two deliberate design choices:

* **The loop is bounded** (``AI_MAX_TOOL_ITERATIONS``). A model that keeps
  calling tools must be cut off by the server, not by the model's own judgement.
* **Summarising and recommending degrade to deterministic answers** when no
  real model is configured. Those two endpoints are useful without an LLM, so
  they must not depend on one.
"""

import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Any
from uuid import uuid4

from src.application.dtos.assistant_dto import (
    Artifact,
    AssistantDone,
    AssistantEvent,
    AssistantTextDelta,
    AssistantToolEvent,
    DeletionOutcome,
    RecommendationItem,
    RecommendationResult,
    SummaryResult,
)
from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.ports.assistant_model_port import AssistantModelResolver
from src.application.ports.assistant_repository_port import (
    AssistantQuotaPort,
    ConversationRepositoryPort,
)
from src.application.ports.llm_port import LlmMessage, LlmPort, LlmToolCall
from src.application.services.assistant_prompts import (
    RECOMMEND_PROMPT,
    SYSTEM_PROMPT,
)
from src.application.services.assistant_tools import (
    AssistantToolBox,
    build_attachment_context,
    format_category,
    format_label,
)
from src.domain.assistant.entities.conversation import (
    Conversation,
    Message,
    MessageRole,
)
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantAttachmentLimitExceeded,
    AssistantConversationNotFound,
    AssistantDeletionNotFound,
    AssistantDisabledError,
    AssistantToolError,
)
from src.domain.assistant.policies.assistant_policy import (
    can_use_assistant,
    hourly_quota,
    max_attachments_for_tier,
    max_tool_iterations_for_tier,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.converters.conversion_map import build_conversion_map

logger = logging.getLogger(__name__)

#: Longest auto-generated conversation title (the sidebar is one line wide).
_MAX_TITLE_CHARS = 80

#: How many candidates the deterministic recommender ranks.
_MAX_RECOMMENDATIONS = 4

#: Rank order used when the model does not rank for us. Formats that are
#: editable, portable and viewable almost everywhere come first — that is the
#: order a competent human would suggest for an unstated goal. Anything not
#: listed keeps the registry's own (alphabetical) order, because the sort is
#: stable.
_PREFERRED_TARGETS: tuple[str, ...] = (
    "txt", "md", "docx", "pdf", "csv", "xlsx", "html", "epub",
    "png", "jpg", "jpeg", "wav", "mp3", "mp4", "zip",
)

#: One-line justification per preferred target, for the fallback ranking.
_DEFAULT_REASONS: dict[str, str] = {
    "txt": "Plain text opens anywhere and is easy to copy from.",
    "md": "Markdown keeps the structure while staying readable as plain text.",
    "docx": "Editable in Word, Google Docs and LibreOffice.",
    "pdf": "Keeps the exact layout for sharing and printing.",
    "csv": "A simple table format every spreadsheet can import.",
    "xlsx": "A real spreadsheet, with formulas and multiple sheets.",
    "html": "Opens in any browser and works well on the web.",
    "epub": "Reflows text to fit any e-reader.",
    "png": "Lossless image, good for screenshots and diagrams.",
    "jpg": "Small image file, good for photos.",
    "jpeg": "Small image file, good for photos.",
    "wav": "Uncompressed audio, lossless but large.",
    "mp3": "Audio that plays on almost every device.",
    "mp4": "Video that plays on almost every device.",
    "zip": "Bundles the files together into one download.",
}

_FALLBACK_CONFIDENCES: tuple[float, ...] = (0.85, 0.7, 0.6, 0.5)

#: Shown when the model used up its tool budget without producing an answer.
_EXHAUSTED_MESSAGE = (
    "I gathered what I could but could not finish that in one go. Could you "
    "narrow it down — for example by naming the file you mean?"
)


def _artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    """Serialize an :class:`Artifact` for storage in a message's ``meta``.

    The key names are a frozen cross-stack contract: the SPA reads the same
    shape back when it reopens a conversation, so renaming or adding a key here
    silently breaks the replayed turn. Persisting the dict (rather than the
    dataclass) keeps ``meta`` JSON-safe for the repository, which stores it as
    a JSON string.
    """
    return {
        "type": artifact.type,
        "id": artifact.id,
        "name": artifact.name,
        "meta": artifact.meta,
    }


def _with_deletion_state(
    meta: dict[str, Any] | None, file_id: str, state: str
) -> dict[str, Any]:
    """Return a copy of a message's ``meta`` with its ``delete`` artifact resolved.

    Only the artifact that is still ``pending`` for ``file_id`` is touched, so
    an earlier, already-resolved proposal for the same file keeps its own
    history. Everything else in the message's meta is preserved verbatim,
    because the same blob also carries the provider tool-call bookkeeping the
    next request needs — dropping it would invalidate the replayed history.
    """
    updated = dict(meta or {})
    raw = updated.get("artifacts")
    if not isinstance(raw, list):
        return updated
    entries: list[Any] = []
    for entry in raw:
        if (
            isinstance(entry, dict)
            and entry.get("type") == "delete"
            and entry.get("id") == file_id
        ):
            entry_meta = entry.get("meta")
            if isinstance(entry_meta, dict) and entry_meta.get("state") == "pending":
                entry = {**entry, "meta": {**entry_meta, "state": state}}
        entries.append(entry)
    updated["artifacts"] = entries
    return updated


def _auto_title(first_message: str) -> str:
    """A conversation title derived from its first user message.

    Derived rather than asked for: a title prompt would add a round-trip and a
    failure mode to every new chat, and the first message is almost always a
    better summary than an LLM-written one would be.
    """
    collapsed = " ".join(first_message.split())
    if not collapsed:
        return "New chat"
    return collapsed[:_MAX_TITLE_CHARS]


def _tool_call_to_meta(call: LlmToolCall) -> dict[str, object]:
    """Serialise one tool call for storage on an assistant message.

    ``thought_signature`` is written only when the provider issued one, so a
    transcript recorded against OpenAI/Groq keeps the exact metadata shape it
    always had (no ``null`` key appears in the stored JSON).
    """
    entry: dict[str, object] = {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments,
    }
    if call.thought_signature:
        entry["thought_signature"] = call.thought_signature
    return entry


def _tool_calls_of(message: Message) -> tuple[LlmToolCall, ...]:
    """Rehydrate the tool calls recorded on a stored assistant message.

    Stored (not recomputed) because a tool call is part of the transcript the
    provider requires to be replayed verbatim: an assistant turn that asked for
    a tool must be followed by that tool's result on the next request, or the
    provider rejects the whole conversation.
    """
    meta = message.meta or {}
    raw = meta.get("tool_calls")
    if not isinstance(raw, list):
        return ()
    calls: list[LlmToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        call_id = entry.get("id")
        name = entry.get("name")
        arguments = entry.get("arguments")
        if not isinstance(call_id, str) or not isinstance(name, str):
            continue
        signature = entry.get("thought_signature")
        calls.append(
            LlmToolCall(
                id=call_id,
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                # Replayed verbatim so a Gemini follow-up turn is accepted; a
                # value stored by a non-Gemini turn simply is not there.
                thought_signature=signature if isinstance(signature, str) and signature else None,
            )
        )
    return tuple(calls)


def _tool_call_id_of(message: Message) -> str | None:
    """The tool call a stored ``tool`` message answers, if recorded."""
    meta = message.meta or {}
    value = meta.get("tool_call_id")
    return value if isinstance(value, str) else None


def _to_llm_message(message: Message) -> LlmMessage:
    """Map one stored message onto the provider-agnostic wire shape."""
    if message.role is MessageRole.TOOL:
        return LlmMessage(
            role="tool",
            content=message.content,
            tool_call_id=_tool_call_id_of(message),
            name=message.tool_name,
        )
    if message.role is MessageRole.ASSISTANT:
        return LlmMessage(
            role="assistant",
            content=message.content,
            tool_calls=_tool_calls_of(message),
        )
    return LlmMessage(role="user", content=message.content)


def build_bounded_history(
    messages: Sequence[Message], max_messages: int
) -> list[LlmMessage]:
    """Replay the newest ``max_messages`` messages as a *valid* provider history.

    Truncating a transcript at an arbitrary point is not safe: a provider
    rejects a ``tool`` result with no preceding tool-call request, and rejects
    an assistant message whose tool calls have no results. So the window is
    taken in whole groups — an assistant message together with every tool result
    it asked for — and a group that does not fit (or was already incomplete) is
    dropped rather than repaired.

    Pure and order-preserving, which is what makes it unit-testable without a
    provider.
    """
    groups: list[list[Message]] = []
    index = 0
    while index < len(messages):
        current = messages[index]
        calls = _tool_calls_of(current) if current.role is MessageRole.ASSISTANT else ()
        if calls:
            expected = {call.id for call in calls}
            group = [current]
            index += 1
            while index < len(messages) and messages[index].role is MessageRole.TOOL:
                group.append(messages[index])
                index += 1
            answered = {
                call_id
                for call_id in (_tool_call_id_of(item) for item in group[1:])
                if call_id is not None
            }
            if expected <= answered:
                groups.append(group)
            # An incomplete group is dropped entirely: half a request/response
            # pair is worse than none at all.
            continue
        if current.role is MessageRole.TOOL:
            # Orphan tool result (its request fell outside the window).
            index += 1
            continue
        groups.append([current])
        index += 1

    selected: list[list[Message]] = []
    used = 0
    for group in reversed(groups):
        if selected and used + len(group) > max_messages:
            break
        selected.append(group)
        used += len(group)
    selected.reverse()
    return [_to_llm_message(item) for group in selected for item in group]


class AssistantService:
    """Runs assistant turns and the standalone summarise/recommend operations."""

    def __init__(
        self,
        *,
        models: AssistantModelResolver,
        toolbox: AssistantToolBox,
        conversations: ConversationRepositoryPort,
        quota: AssistantQuotaPort,
        max_tool_iterations: int = 6,
        max_history_messages: int = 20,
    ) -> None:
        #: The model is resolved per turn from the caller's tier — see
        #: ``model_for_tier``. Holding a single transport here would serve every
        #: plan the same model and make the tier→model mapping meaningless.
        self._models = models
        self._toolbox = toolbox
        self._conversations = conversations
        self._quota = quota
        #: Deployment ceiling on tool round-trips. The effective per-turn bound
        #: is ``min(this, max_tool_iterations_for_tier(tier))``: this is the
        #: operator's hard cap, the policy value is the plan's entitlement.
        self._max_tool_iterations = max_tool_iterations
        self._max_history_messages = max_history_messages

    def model_for_tier(self, tier: SubscriptionTier) -> str:
        """Identifier of the model that serves ``tier`` (for ``/status``).

        A method (not a property) because the model now depends on the plan:
        there is no single "the" model to report.
        """
        return self._models.for_tier(tier).model

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        *,
        user_id: int,
        tier: SubscriptionTier,
        message: str,
        conversation_id: str | None,
        file_ids: list[str] | None = None,
        context: str | None = None,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> AsyncGenerator[AssistantEvent]:
        """Run one turn, yielding deltas, tool events and a terminal event.

        Raises (before the first yield, so the router can still answer with a
        real HTTP status) when the tier has no allowance or the hourly quota is
        spent. A missing/foreign ``conversation_id`` raises
        :class:`AssistantConversationNotFound`, which the router reports as a
        404 — the same response for "not yours" and "does not exist", so
        conversation ids stay unenumerable.

        ``file_ids`` are resolved through the toolbox (which delegates ownership
        to ``FileService``); a missing or foreign id raises
        :class:`AssistantAttachmentNotFound`, also reported as a 404 before the
        stream starts. More than the tier's allowance raises
        :class:`AssistantAttachmentLimitExceeded` (a 403), also before the
        stream starts. ``context`` is untrusted page text and is only ever
        embedded into a prompt.
        """
        if not can_use_assistant(tier):
            raise AssistantDisabledError(
                "The AI assistant is not available on your current plan."
            )
        # Checked before the quota is consumed: an over-cap request is a client
        # error and must not cost the caller one of their paid turns. The limit
        # is read from the single policy (never repeated here), so the number in
        # the message is exactly the number the server enforces.
        attachment_limit = max_attachments_for_tier(tier)
        if len(file_ids or []) > attachment_limit:
            # Pluralised: FREE allows exactly one, so "up to 1 attachments"
            # would reach the user as a visible typo, and this message is shown
            # verbatim by the client.
            noun = "attachment" if attachment_limit == 1 else "attachments"
            raise AssistantAttachmentLimitExceeded(
                f"Your plan allows up to {attachment_limit} {noun} per message."
            )
        # Consumed before any work: a turn whose model call fails still cost the
        # provider, and metering only successful turns would make the limit
        # trivially bypassable by making the assistant fail on purpose.
        await self._quota.consume(user_id, hourly_quota(tier))

        attachments = await self._toolbox.resolve_attachments(user_id, file_ids or [])

        conversation = await self._load_or_create_conversation(
            user_id=user_id, conversation_id=conversation_id, first_message=message
        )
        user_message = await self._append_message(
            conversation.id,
            role=MessageRole.USER,
            content=message,
            # Persisted (not just put in the prompt) because the SPA re-renders
            # an attachment chip from the stored turn. An empty list is stored
            # explicitly so "no attachments" never has to be inferred.
            meta={
                "attachments": [
                    {
                        "id": item.file_id,
                        "name": item.file_name,
                        "extension": item.extension,
                    }
                    for item in attachments
                ]
            },
        )

        history = await self._conversations.list_messages(
            conversation.id, limit=self._max_history_messages
        )
        llm_messages = [LlmMessage(role="system", content=SYSTEM_PROMPT)]
        prompt_context = build_attachment_context(attachments, context)
        if prompt_context:
            # Inserted AFTER the system prompt and BEFORE the history: it is
            # server-authored guidance for this turn, not part of what the user
            # said, and it must not push the system prompt out of the prompt.
            llm_messages.append(LlmMessage(role="system", content=prompt_context))
        llm_messages.extend(build_bounded_history(history, self._max_history_messages))
        tools = self._toolbox.specs()

        artifacts: list[Artifact] = []
        collected: list[Artifact] = []

        # The tier's transport, resolved once per turn so every round-trip of
        # the loop talks to the same plan's model. The loop bound is the
        # deployment ceiling capped by the plan's entitlement: a FREE turn may
        # make at most 4 round-trips even where the server allows 6, and no plan
        # may exceed the operator's ceiling. ``max(1, ...)`` guarantees at least
        # one completion so a misconfigured pair cannot produce an empty turn.
        llm = self._models.for_tier(tier)
        max_iterations = max(
            1, min(self._max_tool_iterations, max_tool_iterations_for_tier(tier))
        )

        for _iteration in range(max_iterations):
            response_text = ""
            tool_calls: tuple[LlmToolCall, ...] = ()
            async for chunk in llm.stream(messages=llm_messages, tools=tools):
                if chunk.kind == "text":
                    if chunk.text:
                        response_text += chunk.text
                        yield AssistantTextDelta(text=chunk.text)
                elif chunk.response is not None:
                    tool_calls = chunk.response.tool_calls
                    if chunk.response.content:
                        response_text = chunk.response.content

            if not tool_calls:
                persisted = await self._append_message(
                    conversation.id,
                    role=MessageRole.ASSISTANT,
                    content=response_text,
                    meta={"artifacts": [_artifact_to_dict(a) for a in collected]},
                )
                yield AssistantDone(
                    conversation_id=conversation.id,
                    message_id=persisted.id,
                    content=response_text,
                    artifacts=tuple(collected),
                    user_message_id=user_message.id,
                )
                return

            await self._record_tool_request(conversation.id, response_text, tool_calls)
            llm_messages.append(
                LlmMessage(role="assistant", content=response_text, tool_calls=tool_calls)
            )

            for call in tool_calls:
                label = self._toolbox.label_for(call.name)
                yield AssistantToolEvent(name=call.name, label=label, status="running")
                before = len(artifacts)
                result = await self._toolbox.execute(
                    call.name,
                    call.arguments,
                    user_id=user_id,
                    tier=tier,
                    artifacts=artifacts,
                    # The delete proposal records which chat it belongs to, so
                    # the artifact can be resolved from a component (the mini
                    # chat) that does not know the conversation id itself.
                    conversation_id=conversation.id,
                    # How this HTTP request authenticated, so a conversion the
                    # assistant starts is labelled API for an X-API-Key turn.
                    origin=origin,
                )
                produced = list(artifacts[before:])
                collected.extend(produced)
                summary = self._toolbox.describe(call.name, result)
                yield AssistantToolEvent(
                    name=call.name,
                    label=label,
                    status="done",
                    summary=summary,
                    artifacts=tuple(produced),
                )
                payload = json.dumps(result, default=str)
                llm_messages.append(
                    LlmMessage(
                        role="tool",
                        content=payload,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
                await self._append_message(
                    conversation.id,
                    role=MessageRole.TOOL,
                    content=payload,
                    tool_name=call.name,
                    meta={
                        "tool_call_id": call.id,
                        "label": label,
                        "summary": summary,
                        "artifacts": [_artifact_to_dict(a) for a in produced],
                    },
                )

        persisted = await self._append_message(
            conversation.id,
            role=MessageRole.ASSISTANT,
            content=_EXHAUSTED_MESSAGE,
            meta={"artifacts": [_artifact_to_dict(a) for a in collected]},
        )
        yield AssistantDone(
            conversation_id=conversation.id,
            message_id=persisted.id,
            content=_EXHAUSTED_MESSAGE,
            artifacts=tuple(collected),
            user_message_id=user_message.id,
        )

    async def _load_or_create_conversation(
        self, *, user_id: int, conversation_id: str | None, first_message: str
    ) -> Conversation:
        if conversation_id:
            existing = await self._conversations.get_conversation(conversation_id, user_id)
            if existing is None:
                raise AssistantConversationNotFound("Conversation not found")
            return existing
        return await self._conversations.create_conversation(
            user_id=user_id, title=_auto_title(first_message)
        )

    async def _append_message(
        self,
        conversation_id: str,
        *,
        role: MessageRole,
        content: str,
        tool_name: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Message:
        """Persist one transcript entry.

        The id is minted here so the caller can return it to the client
        immediately; ``position`` is left to the repository, which is the only
        party that can assign it without a race.
        """
        return await self._conversations.add_message(
            Message(
                id=str(uuid4()),
                conversation_id=conversation_id,
                position=0,
                role=role,
                content=content,
                tool_name=tool_name,
                meta=meta,
            )
        )

    async def _record_tool_request(
        self, conversation_id: str, content: str, tool_calls: tuple[LlmToolCall, ...]
    ) -> None:
        """Store the assistant turn that requested tools, calls included.

        The stored ``tool_calls`` are what make the next request valid: they are
        replayed verbatim so the tool results that follow have something to
        attach to.
        """
        await self._append_message(
            conversation_id,
            role=MessageRole.ASSISTANT,
            content=content,
            meta={
                "tool_calls": [
                    _tool_call_to_meta(call) for call in tool_calls
                ]
            },
        )

    # ------------------------------------------------------------------
    # Standalone operations
    # ------------------------------------------------------------------

    async def resolve_deletion(
        self,
        *,
        user_id: int,
        conversation_id: str,
        file_id: str,
        approve: bool,
    ) -> DeletionOutcome:
        """Execute or dismiss an AI-proposed file deletion.

        This is the SECOND half of the two-phase deletion handshake: the model
        can only *propose* a deletion (see ``AssistantToolBox._delete_file``),
        and the file is removed here only because the authenticated owner
        clicked Confirm. Requiring a live ``pending`` proposal in the caller's
        own conversation is what stops this from being a blind "delete any file
        I own" API, and it makes a second confirm (or a cancel after a confirm)
        resolve to not-found rather than acting twice.

        Deliberately does NOT append a transcript message: an orphan
        ``MessageRole.TOOL`` row would be silently dropped by
        ``build_bounded_history`` (so it would be pointless), and appending a
        USER row would fabricate speech the user never typed. Rewriting the
        artifact's state via ``update_message_meta`` is the whole record — and
        it is what stops a page reload from re-showing a live Confirm prompt
        for a decision already made.
        """
        conversation = await self._conversations.get_conversation(
            conversation_id, user_id
        )
        if conversation is None:
            raise AssistantConversationNotFound("Conversation not found")

        messages = await self._conversations.list_messages(conversation.id)
        pending = self._pending_deletion(messages, file_id)
        if pending is None:
            raise AssistantDeletionNotFound(
                "There is no deletion waiting to be confirmed for that file."
            )
        message, artifact = pending

        if approve:
            try:
                file_name = await self._toolbox.delete_owned_file(user_id, file_id)
                state = "deleted"
            except FileRecordNotFoundError:
                # The file was already removed between the proposal and the
                # click (another tab, another device). "It is already gone" is a
                # resolved outcome, not a server error, so it is reported as a
                # failed attempt rather than raised.
                file_name = artifact.name
                state = "failed"
        else:
            # Cancelling deletes nothing and touches no file: it is purely the
            # user dismissing the prompt.
            file_name = artifact.name
            state = "cancelled"

        await self._conversations.update_message_meta(
            message.id, _with_deletion_state(message.meta, file_id, state)
        )
        return DeletionOutcome(file_id=file_id, file_name=file_name, state=state)

    @staticmethod
    def _pending_deletion(
        messages: Sequence[Message], file_id: str
    ) -> tuple[Message, Artifact] | None:
        """Newest assistant message carrying a ``pending`` delete artifact.

        Scanned newest-first so a file proposed, cancelled and proposed again
        resolves against the latest proposal. Only assistant messages are
        considered: the artifact is recorded on the turn the model requested the
        tool, and accepting it from any other role would let a crafted tool
        result masquerade as a user-facing proposal.
        """
        for message in reversed(list(messages)):
            if message.role is not MessageRole.ASSISTANT:
                continue
            raw = (message.meta or {}).get("artifacts")
            if not isinstance(raw, list):
                continue
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "delete" or entry.get("id") != file_id:
                    continue
                meta = entry.get("meta")
                if not isinstance(meta, dict) or meta.get("state") != "pending":
                    continue
                name = entry.get("name")
                return message, Artifact(
                    type="delete",
                    id=file_id,
                    name=name if isinstance(name, str) else "",
                    meta=dict(meta),
                )
        return None

    async def summarize_file(
        self, *, user_id: int, tier: SubscriptionTier, file_id: str
    ) -> SummaryResult:
        """Summarise one owned document.

        Raises :class:`AssistantToolError` (mapped to a 4xx) when the file is
        unreadable, rather than returning an empty summary — the caller is a
        synchronous request with no model to explain the silence.

        ``tier`` both caps the readable size (via the toolbox) and chooses the
        model, so ``model`` in the response is the plan's model, not a global
        default.

        Metered exactly like a chat turn (tier gate + one hourly request). This
        is the SAME billable provider call as ``stream_chat`` makes, so leaving
        it unmetered would let any authenticated caller make unlimited paid
        completions — and would make the hourly quota the pricing page sells
        trivially bypassable by moving from /chat to /summarize.
        """
        if not can_use_assistant(tier):
            raise AssistantDisabledError(
                "The AI assistant is not available on your current plan."
            )
        await self._quota.consume(user_id, hourly_quota(tier))

        loaded = await self._toolbox.read_document(user_id=user_id, file_id=file_id, tier=tier)
        if isinstance(loaded, dict):
            raise AssistantToolError(str(loaded.get("error", "The file could not be read.")))
        row, document = loaded
        if not document.supported:
            raise AssistantToolError(
                f"The text of {row.file_name} cannot be read: {document.note}"
            )
        if not document.text.strip():
            raise AssistantToolError(f"{row.file_name} contains no readable text.")
        result = await self._toolbox.summarize_text(document.text, tier=tier)
        points = result.get("key_points")
        return SummaryResult(
            file_id=row.id,
            file_name=row.file_name,
            summary=str(result.get("summary", "")),
            key_points=[str(point) for point in points] if isinstance(points, list) else [],
            model=self.model_for_tier(tier),
        )

    async def recommend(
        self,
        *,
        user_id: int,
        tier: SubscriptionTier,
        file_id: str | None,
        source_format: str | None,
        use_case: str | None,
    ) -> RecommendationResult:
        """Recommend target formats for a file (or a bare source format).

        Works with no model configured: the ranking falls back to a
        deterministic order, so the endpoint is usable in development and is
        byte-for-byte reproducible in tests. ``tier`` selects the model that may
        rank the candidates.

        Metered like a chat turn for the same reason as ``summarize_file``: the
        model call is billable, and an unmetered endpoint would be a free
        bypass of the plan's allowance.
        """
        if not can_use_assistant(tier):
            raise AssistantDisabledError(
                "The AI assistant is not available on your current plan."
            )
        await self._quota.consume(user_id, hourly_quota(tier))

        conversion_map = build_conversion_map()
        resolved = (source_format or "").strip().lstrip(".").lower() or None
        if resolved is None and file_id is not None:
            resolved = (
                await self._toolbox.source_format_for(user_id=user_id, file_id=file_id)
            ) or None
        if resolved is None:
            raise AssistantToolError(
                "Provide either a file or a source format to recommend a conversion for."
            )

        candidates = conversion_map.get(resolved)
        if not candidates:
            raise AssistantToolError(
                f"'{resolved}' is not a format this service can convert."
            )

        ranked = await self._rank_targets(resolved, candidates, use_case, tier=tier)
        return RecommendationResult(
            source_format=resolved,
            use_case=use_case,
            recommendations=ranked,
        )

    async def _rank_targets(
        self,
        source_format: str,
        candidates: list[str],
        use_case: str | None,
        *,
        tier: SubscriptionTier,
    ) -> list[RecommendationItem]:
        """Rank target formats, preferring the model's answer and degrading well."""
        ranked: list[tuple[str, str, float]] = []
        llm = self._models.for_tier(tier)
        if llm.model != "echo":
            ranked = await self._llm_rank(source_format, candidates, use_case, llm=llm)
        if not ranked:
            ranked = _deterministic_ranking(candidates)
        return [
            RecommendationItem(
                target_format=target,
                label=format_label(target),
                category=format_category(target),
                reason=reason,
                confidence=confidence,
            )
            for target, reason, confidence in ranked[:_MAX_RECOMMENDATIONS]
        ]

    async def _llm_rank(
        self,
        source_format: str,
        candidates: list[str],
        use_case: str | None,
        *,
        llm: LlmPort,
    ) -> list[tuple[str, str, float]]:
        """Ask ``llm`` to rank the candidates; ``[]`` when it does not cooperate."""
        prompt = (
            f"Source format: {source_format}\n"
            f"Available target formats: {', '.join(candidates)}\n"
            f"User's goal: {use_case or '(not stated)'}"
        )
        messages = [
            LlmMessage(role="system", content=RECOMMEND_PROMPT),
            LlmMessage(role="user", content=prompt),
        ]
        raw = ""
        async for chunk in llm.stream(messages=messages, tools=[]):
            if chunk.kind == "text":
                raw += chunk.text
            elif chunk.response is not None and chunk.response.content:
                raw = chunk.response.content

        parsed = _parse_json_object(raw)
        if parsed is None:
            return []
        entries = parsed.get("recommendations")
        if not isinstance(entries, list):
            return []

        allowed = {candidate.lower(): candidate for candidate in candidates}
        ranked: list[tuple[str, str, float]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            suggested = entry.get("target_format")
            if not isinstance(suggested, str):
                continue
            target = allowed.get(suggested.strip().lstrip(".").lower())
            if target is None or any(item[0] == target for item in ranked):
                # A format the registry does not support is dropped, never
                # shown: the whole point of the prompt rule is that the user is
                # only offered conversions that will actually run.
                continue
            reason = entry.get("reason")
            confidence = entry.get("confidence")
            ranked.append(
                (
                    target,
                    reason if isinstance(reason, str) and reason.strip() else _DEFAULT_REASONS.get(target, "A good fit for that goal."),
                    _clamp_confidence(confidence),
                )
            )
        return ranked


def _clamp_confidence(value: Any) -> float:
    """Coerce a model-supplied confidence into ``[0, 1]``.

    Models routinely answer with percentages (``80``) or strings; clamping keeps
    the contract true (0..1) instead of leaking a number the UI would render as
    "8000%".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.5
    number = float(value)
    if number > 1.0:
        number = number / 100.0 if number > 1.0 and number <= 100.0 else 1.0
    return max(0.0, min(number, 1.0))


def _deterministic_ranking(candidates: list[str]) -> list[tuple[str, str, float]]:
    """Rank candidates without a model, in the order a person would suggest."""
    ordered = sorted(
        candidates,
        key=lambda fmt: (
            _PREFERRED_TARGETS.index(fmt) if fmt in _PREFERRED_TARGETS else len(_PREFERRED_TARGETS)
        ),
    )
    return [
        (
            fmt,
            _DEFAULT_REASONS.get(fmt, "A widely supported format for this kind of file."),
            _FALLBACK_CONFIDENCES[index] if index < len(_FALLBACK_CONFIDENCES) else 0.4,
        )
        for index, fmt in enumerate(ordered[:_MAX_RECOMMENDATIONS])
    ]


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a model reply as a JSON object, tolerating a Markdown fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed: Any = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
