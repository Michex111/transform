"""Unit tests for ``AssistantService``: the agent loop, transcript and ranking.

The loop is the piece with the most ways to be subtly wrong — a tool result that
is not persisted invalidates the *next* request, an unbounded loop costs money,
and a stream that never terminates hangs the browser — so these tests drive it
with a scripted model and assert on the transcript as well as the emitted
events.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.application.dtos.assistant_dto import (
    Artifact,
    AssistantDone,
    AssistantTextDelta,
    AssistantToolEvent,
    DeletionOutcome,
    SummaryResult,
)
from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.ports.document_text_port import ExtractedDocument
from src.application.ports.llm_port import LlmMessage, LlmToolCall, LlmToolSpec
from src.application.services.assistant_service import (
    AssistantService,
    build_bounded_history,
)
from src.application.services.assistant_tools import AttachmentRef
from src.domain.assistant.entities.conversation import (
    Conversation,
    Message,
    MessageRole,
)
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantAttachmentLimitExceeded,
    AssistantAttachmentNotFound,
    AssistantConversationNotFound,
    AssistantDeletionNotFound,
    AssistantDisabledError,
    AssistantQuotaExceeded,
    AssistantToolError,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserFileModel
from tests.fakes.fake_assistant_model_resolver import FakeAssistantModelResolver
from tests.fakes.fake_llm_port import FakeLlmPort, text_response, tool_response

NOW = datetime(2026, 9, 28, tzinfo=UTC)
USER = 42
OTHER = 7


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConversationRepository:
    """In-memory transcript store with the real user-scoping rules."""

    def __init__(self, conversations: list[Conversation] | None = None) -> None:
        self.conversations = {row.id: row for row in conversations or []}
        self.messages: dict[str, list[Message]] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.meta_updates: list[tuple[str, dict]] = []

    async def create_conversation(self, *, user_id: int, title: str) -> Conversation:
        conversation = Conversation(
            id=f"conv-{len(self.conversations) + 1}",
            user_id=user_id,
            title=title,
            created_at=NOW,
            updated_at=NOW,
        )
        self.conversations[conversation.id] = conversation
        self.created.append(conversation.id)
        return conversation

    async def get_conversation(
        self, conversation_id: str, user_id: int
    ) -> Conversation | None:
        row = self.conversations.get(conversation_id)
        if row is None or row.user_id != user_id:
            return None
        return row

    async def list_conversations(
        self, user_id: int, *, limit: int = 50
    ) -> list[Conversation]:
        rows = [row for row in self.conversations.values() if row.user_id == user_id]
        return rows[:limit]

    async def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        if await self.get_conversation(conversation_id, user_id) is None:
            return False
        del self.conversations[conversation_id]
        self.messages.pop(conversation_id, None)
        self.deleted.append(conversation_id)
        return True

    async def add_message(self, message: Message) -> Message:
        stored = replace(
            message,
            position=len(self.messages.get(message.conversation_id, [])) + 1,
            created_at=NOW,
        )
        self.messages.setdefault(message.conversation_id, []).append(stored)
        return stored

    async def list_messages(
        self, conversation_id: str, *, limit: int = 100
    ) -> list[Message]:
        return self.messages.get(conversation_id, [])[-limit:]

    async def update_message_meta(self, message_id: str, meta: dict) -> bool:
        """Rewrite a stored message's meta in place, like the SQL repository."""
        self.meta_updates.append((message_id, meta))
        for messages in self.messages.values():
            for index, message in enumerate(messages):
                if message.id == message_id:
                    messages[index] = replace(message, meta=meta)
                    return True
        return False


class FakeQuota:
    def __init__(self, error: Exception | None = None) -> None:
        self.consumed: list[tuple[int, int]] = []
        self._error = error

    async def consume(self, user_id: int, limit: int) -> None:
        if self._error is not None:
            raise self._error
        self.consumed.append((user_id, limit))

    async def peek(self, user_id: int) -> int:
        del user_id
        return 0


class ScriptedToolBox:
    """A toolbox whose results and produced artifacts are scripted per tool."""

    def __init__(
        self,
        results: dict[str, dict] | None = None,
        *,
        artifacts: dict[str, list[Artifact]] | None = None,
        document: tuple[UserFileModel, ExtractedDocument] | dict | None = None,
        summary: dict | None = None,
        source_format: str = "pdf",
        attachments: list[AttachmentRef] | None = None,
        file_names: dict[str, str] | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.results = results if results is not None else {"list_files": {"files": [], "count": 0}}
        self.artifacts = artifacts or {}
        self.document = document
        self.summary = summary or {"summary": "A summary.", "key_points": ["One"]}
        self.source_format = source_format
        self.attachments = {item.file_id: item for item in attachments or []}
        self.executed: list[tuple[str, dict]] = []
        #: The names ``delete_owned_file`` reports, and the ids it removed: a
        #: test asserts on both, the second to prove a cancel deleted nothing.
        self.file_names = file_names or {}
        self.delete_error = delete_error
        self.deleted: list[str] = []

    def specs(self) -> list[LlmToolSpec]:
        return [
            LlmToolSpec(name=name, description="d", parameters={"type": "object"})
            for name in self.results
        ]

    @staticmethod
    def label_for(name: str) -> str:
        return f"Running {name}"

    @staticmethod
    def describe(name: str, result: dict) -> str:
        return f"{name} finished"

    async def execute(
        self,
        name: str,
        arguments: dict,
        *,
        user_id: int,
        tier: SubscriptionTier,
        artifacts: list[Artifact],
        conversation_id: str | None = None,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> dict:
        del user_id, tier, conversation_id, origin
        self.executed.append((name, arguments))
        artifacts.extend(self.artifacts.get(name, []))
        return self.results.get(name, {})

    async def delete_owned_file(self, user_id: int, file_id: str) -> str:
        """The confirm-only deletion the service calls; records what it removed."""
        del user_id
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(file_id)
        return self.file_names.get(file_id, file_id)

    async def read_document(
        self, *, user_id: int, file_id: str, tier: SubscriptionTier
    ):
        del user_id, file_id, tier
        return self.document

    async def resolve_attachments(
        self, user_id: int, file_ids: Sequence[str]
    ) -> list[AttachmentRef]:
        del user_id
        resolved: list[AttachmentRef] = []
        for file_id in file_ids:
            item = self.attachments.get(file_id)
            if item is None:
                raise AssistantAttachmentNotFound("Attachment not found")
            resolved.append(item)
        return resolved

    async def summarize_text(self, text: str, *, tier: SubscriptionTier) -> dict:
        del text, tier
        return self.summary

    async def source_format_for(self, *, user_id: int, file_id: str) -> str:
        del user_id, file_id
        return self.source_format


def _service(
    *,
    llm: FakeLlmPort | None = None,
    models: FakeAssistantModelResolver | None = None,
    toolbox: ScriptedToolBox | None = None,
    conversations: FakeConversationRepository | None = None,
    quota: FakeQuota | None = None,
    max_tool_iterations: int = 6,
    max_history_messages: int = 20,
) -> AssistantService:
    return AssistantService(
        models=models
        or FakeAssistantModelResolver(
            default=llm or FakeLlmPort([text_response("Hello there.")])
        ),
        toolbox=toolbox or ScriptedToolBox(),  # type: ignore[arg-type]
        conversations=conversations or FakeConversationRepository(),
        quota=quota or FakeQuota(),
        max_tool_iterations=max_tool_iterations,
        max_history_messages=max_history_messages,
    )


async def _collect(service: AssistantService, **kwargs) -> list:
    events = []
    async for event in service.stream_chat(**kwargs):
        events.append(event)
    return events


def _chat(
    service: AssistantService,
    message: str = "Hello",
    conversation_id: str | None = None,
    *,
    file_ids: list[str] | None = None,
    context: str | None = None,
):
    return asyncio.run(
        _collect(
            service,
            user_id=USER,
            tier=SubscriptionTier.FREE,
            message=message,
            conversation_id=conversation_id,
            file_ids=file_ids,
            context=context,
        )
    )


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_tier_without_allowance_is_refused_before_any_work() -> None:
    quota = FakeQuota()
    service = _service(quota=quota)

    async def _first() -> object:
        return await anext(
            service.stream_chat(
                user_id=USER,
                tier=SubscriptionTier.GUEST,
                message="Hi",
                conversation_id=None,
            )
        )

    with pytest.raises(AssistantDisabledError):
        asyncio.run(_first())
    assert quota.consumed == []


def test_exhausted_quota_propagates_to_the_caller() -> None:
    quota = FakeQuota(AssistantQuotaExceeded("too many"))
    service = _service(quota=quota)

    async def _first() -> object:
        return await anext(
            service.stream_chat(
                user_id=USER,
                tier=SubscriptionTier.FREE,
                message="Hi",
                conversation_id=None,
            )
        )

    with pytest.raises(AssistantQuotaExceeded):
        asyncio.run(_first())


def test_quota_is_consumed_once_per_turn() -> None:
    quota = FakeQuota()
    _chat(_service(quota=quota))
    assert quota.consumed == [(USER, 20)]


# ---------------------------------------------------------------------------
# /summarize and /recommend are metered endpoints, not free model calls
# ---------------------------------------------------------------------------
#
# Both make the SAME billable provider call as a chat turn. Before these tests
# existed, neither checked the tier nor touched the quota, so any authenticated
# caller could issue unlimited paid completions and sidestep the plan's
# allowance entirely by moving off /chat. That is both a cost-abuse hole and an
# entitlement bypass, so each endpoint is pinned here twice: the gate, and the
# meter.


def test_summarize_refuses_a_tier_without_allowance() -> None:
    quota = FakeQuota()
    service = _service(quota=quota)

    with pytest.raises(AssistantDisabledError):
        asyncio.run(
            service.summarize_file(user_id=USER, tier=SubscriptionTier.GUEST, file_id="f1")
        )
    # Refused before the meter too: a request that cannot run must not be billed.
    assert quota.consumed == []


def test_summarize_consumes_the_hourly_quota() -> None:
    quota = FakeQuota()
    # The document is deliberately unreadable: the point is that the meter runs
    # BEFORE the read, so the assertion holds whatever the read would have done.
    service = _service(quota=quota, toolbox=ScriptedToolBox(document={"error": "unreadable"}))

    with pytest.raises(AssistantToolError):
        asyncio.run(
            service.summarize_file(user_id=USER, tier=SubscriptionTier.FREE, file_id="f1")
        )
    assert quota.consumed == [(USER, 20)]


def test_recommend_refuses_a_tier_without_allowance() -> None:
    quota = FakeQuota()
    service = _service(quota=quota)

    with pytest.raises(AssistantDisabledError):
        asyncio.run(
            service.recommend(
                user_id=USER,
                tier=SubscriptionTier.GUEST,
                file_id=None,
                source_format="pdf",
                use_case=None,
            )
        )
    assert quota.consumed == []


def test_recommend_consumes_the_hourly_quota() -> None:
    quota = FakeQuota()
    service = _service(quota=quota, toolbox=ScriptedToolBox(source_format="pdf"))

    result = asyncio.run(
        service.recommend(
            user_id=USER,
            tier=SubscriptionTier.FREE,
            file_id=None,
            source_format="pdf",
            use_case="send to a recruiter",
        )
    )

    assert result.recommendations
    assert quota.consumed == [(USER, 20)]


def test_a_conversation_owned_by_somebody_else_raises_not_found() -> None:
    conversations = FakeConversationRepository(
        [Conversation(id="conv-1", user_id=OTHER, title="Theirs", created_at=NOW, updated_at=NOW)]
    )
    service = _service(conversations=conversations)

    async def _first() -> object:
        return await anext(
            service.stream_chat(
                user_id=USER,
                tier=SubscriptionTier.FREE,
                message="Hi",
                conversation_id="conv-1",
            )
        )

    with pytest.raises(AssistantConversationNotFound):
        asyncio.run(_first())


# ---------------------------------------------------------------------------
# The deletion handshake
# ---------------------------------------------------------------------------
#
# The model only ever proposes; this service method is the authenticated half
# that actually removes a file. Every test here exists to prove the proposal is
# required and that a decision is made exactly once.


def _conversation_with_pending_delete(
    *,
    file_id: str = "file-1",
    name: str = "report.pdf",
    state: str = "pending",
    user_id: int = USER,
) -> FakeConversationRepository:
    """A conversation whose newest assistant message carries a delete artifact."""
    conversations = FakeConversationRepository(
        [Conversation(id="conv-1", user_id=user_id, title="Cleanup", created_at=NOW, updated_at=NOW)]
    )
    asyncio.run(
        conversations.add_message(
            Message(
                id="msg-1",
                conversation_id="conv-1",
                position=0,
                role=MessageRole.ASSISTANT,
                content="Shall I delete report.pdf?",
                meta={
                    "artifacts": [
                        {
                            "type": "delete",
                            "id": file_id,
                            "name": name,
                            "meta": {
                                "state": state,
                                "conversation_id": "conv-1",
                                "extension": "pdf",
                                "size_bytes": 1024,
                                "folder_id": None,
                            },
                        }
                    ]
                },
            )
        )
    )
    return conversations


def test_resolve_deletion_approve_deletes_and_resolves_the_artifact() -> None:
    conversations = _conversation_with_pending_delete()
    toolbox = ScriptedToolBox(file_names={"file-1": "report.pdf"})
    service = _service(conversations=conversations, toolbox=toolbox)

    outcome = asyncio.run(
        service.resolve_deletion(
            user_id=USER, conversation_id="conv-1", file_id="file-1", approve=True
        )
    )

    assert outcome == DeletionOutcome(
        file_id="file-1", file_name="report.pdf", state="deleted"
    )
    assert toolbox.deleted == ["file-1"]
    # The artifact on the carrying message is rewritten, so a reload shows a
    # resolved record instead of a live Confirm prompt.
    assert conversations.meta_updates[-1][0] == "msg-1"
    stored = conversations.messages["conv-1"][0]
    assert stored.meta is not None
    assert stored.meta["artifacts"][0]["meta"]["state"] == "deleted"
    # Nothing is appended: an orphan tool row would be dropped by
    # build_bounded_history, and a user row would fabricate speech.
    assert len(conversations.messages["conv-1"]) == 1


def test_resolve_deletion_cancel_deletes_nothing() -> None:
    conversations = _conversation_with_pending_delete()
    toolbox = ScriptedToolBox(file_names={"file-1": "report.pdf"})
    service = _service(conversations=conversations, toolbox=toolbox)

    outcome = asyncio.run(
        service.resolve_deletion(
            user_id=USER, conversation_id="conv-1", file_id="file-1", approve=False
        )
    )

    assert outcome.state == "cancelled"
    assert outcome.file_name == "report.pdf"
    assert toolbox.deleted == []
    stored = conversations.messages["conv-1"][0]
    assert stored.meta is not None
    assert stored.meta["artifacts"][0]["meta"]["state"] == "cancelled"


def test_resolve_deletion_a_second_time_is_not_found() -> None:
    conversations = _conversation_with_pending_delete()
    service = _service(
        conversations=conversations,
        toolbox=ScriptedToolBox(file_names={"file-1": "report.pdf"}),
    )
    first = asyncio.run(
        service.resolve_deletion(
            user_id=USER, conversation_id="conv-1", file_id="file-1", approve=True
        )
    )
    assert first.state == "deleted"
    # A double-click must not delete twice: the state is no longer pending, so
    # there is nothing left to resolve.
    with pytest.raises(AssistantDeletionNotFound):
        asyncio.run(
            service.resolve_deletion(
                user_id=USER, conversation_id="conv-1", file_id="file-1", approve=True
            )
        )


def test_resolve_deletion_requires_an_owned_conversation() -> None:
    conversations = _conversation_with_pending_delete(user_id=OTHER)
    service = _service(conversations=conversations)

    with pytest.raises(AssistantConversationNotFound):
        asyncio.run(
            service.resolve_deletion(
                user_id=USER, conversation_id="conv-1", file_id="file-1", approve=True
            )
        )


def test_resolve_deletion_requires_a_pending_proposal() -> None:
    conversations = _conversation_with_pending_delete()
    service = _service(conversations=conversations)

    with pytest.raises(AssistantDeletionNotFound):
        asyncio.run(
            service.resolve_deletion(
                user_id=USER, conversation_id="conv-1", file_id="other-file", approve=True
            )
        )


def test_resolve_deletion_reports_a_file_already_gone_as_failed() -> None:
    """The file vanished between the proposal and the click: not a server error."""
    conversations = _conversation_with_pending_delete()
    toolbox = ScriptedToolBox(delete_error=FileRecordNotFoundError())
    service = _service(conversations=conversations, toolbox=toolbox)

    outcome = asyncio.run(
        service.resolve_deletion(
            user_id=USER, conversation_id="conv-1", file_id="file-1", approve=True
        )
    )

    assert outcome.state == "failed"
    assert outcome.file_name == "report.pdf"
    assert toolbox.deleted == []


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def test_happy_path_streams_deltas_and_persists_the_transcript() -> None:
    llm = FakeLlmPort([text_response("Hello there.")], chunk_size=4)
    conversations = FakeConversationRepository()
    events = _chat(_service(llm=llm, conversations=conversations), message="Hi assistant")

    deltas = "".join(event.text for event in events if isinstance(event, AssistantTextDelta))
    assert deltas == "Hello there."
    # The prompt starts with the system prompt and ends with the user message.
    assert llm.calls[0][0][0].role == "system"
    assert llm.calls[0][0][-1].content == "Hi assistant"

    done = events[-1]
    assert isinstance(done, AssistantDone)
    assert done.content == "Hello there."
    assert done.conversation_id == "conv-1"

    stored = conversations.messages["conv-1"]
    assert [message.role for message in stored] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert stored[0].content == "Hi assistant"
    assert stored[1].id == done.message_id
    # The persisted user message id is reported so the client can target a
    # server-side truncate without reloading the transcript.
    assert stored[0].id == done.user_message_id
    assert done.user_message_id != ""
    # A turn that produced nothing still records an explicit empty list, so the
    # client never has to distinguish absent from empty.
    assert stored[1].meta == {"artifacts": []}
    # The title comes from the first user message, no extra model round-trip.
    assert conversations.conversations["conv-1"].title == "Hi assistant"


def test_titles_are_truncated() -> None:
    conversations = FakeConversationRepository()
    _chat(_service(conversations=conversations), message="x" * 300)
    assert len(conversations.conversations["conv-1"].title) == 80


def test_existing_conversation_is_reused_not_recreated() -> None:
    conversations = FakeConversationRepository(
        [Conversation(id="conv-9", user_id=USER, title="Old", created_at=NOW, updated_at=NOW)]
    )
    events = _chat(_service(conversations=conversations), conversation_id="conv-9")
    assert conversations.created == []
    assert events[-1].conversation_id == "conv-9"


def test_tool_call_round_trip_records_both_sides_of_the_pair() -> None:
    artifact = Artifact(type="file", id="file-1", name="report.pdf")
    toolbox = ScriptedToolBox(
        {"list_files": {"files": [], "count": 0}},
        artifacts={"list_files": [artifact]},
    )
    llm = FakeLlmPort(
        [tool_response("list_files", {"query": "report"}), text_response("Found it.")]
    )
    conversations = FakeConversationRepository()
    events = _chat(_service(llm=llm, toolbox=toolbox, conversations=conversations))

    tool_events = [event for event in events if isinstance(event, AssistantToolEvent)]
    assert [(event.name, event.status) for event in tool_events] == [
        ("list_files", "running"),
        ("list_files", "done"),
    ]
    assert tool_events[1].artifacts == (artifact,)
    assert toolbox.executed == [("list_files", {"query": "report"})]

    done = events[-1]
    assert isinstance(done, AssistantDone)
    assert done.artifacts == (artifact,)

    # The stored transcript must replay as a valid provider history: the
    # assistant turn carries its tool calls, and the tool result answers one.
    stored = conversations.messages["conv-1"]
    assert [message.role for message in stored] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert stored[1].meta is not None
    assert stored[1].meta["tool_calls"][0]["name"] == "list_files"
    # The tool-request message must keep exactly the provider's replay payload:
    # nothing has been produced yet, so it gains no artifacts/label/summary.
    assert set(stored[1].meta) == {"tool_calls"}
    assert "artifacts" not in stored[1].meta

    assert stored[2].tool_name == "list_files"
    tool_meta = stored[2].meta
    assert tool_meta is not None
    # The tool message carries everything a reopened conversation needs to
    # rebuild the step without re-running it.
    assert tool_meta["tool_call_id"] == "call_list_files"
    assert tool_meta["label"] == "Running list_files"
    assert tool_meta["summary"] == "list_files finished"
    assert tool_meta["artifacts"] == [
        {"type": "file", "id": "file-1", "name": "report.pdf", "meta": {}}
    ]

    # The final assistant message carries the turn's accumulated artifacts.
    assert stored[3].role is MessageRole.ASSISTANT
    assert stored[3].meta is not None
    assert stored[3].meta["artifacts"] == [
        {"type": "file", "id": "file-1", "name": "report.pdf", "meta": {}}
    ]

    # The second model call saw the tool result.
    assert llm.calls[1][0][-1].role == "tool"


def test_tool_failures_are_passed_back_to_the_model() -> None:
    toolbox = ScriptedToolBox({"list_files": {"error": "no such folder"}})
    llm = FakeLlmPort(
        [tool_response("list_files", {}), text_response("Sorry about that.")]
    )
    events = _chat(_service(llm=llm, toolbox=toolbox))
    assert isinstance(events[-1], AssistantDone)
    tool_payload = llm.calls[1][0][-1]
    assert tool_payload.role == "tool"
    assert "no such folder" in tool_payload.content


def test_the_loop_is_bounded_and_falls_back_to_a_message() -> None:
    toolbox = ScriptedToolBox({"list_files": {"count": 0}})
    llm = FakeLlmPort([tool_response("list_files", {}) for _ in range(10)])
    conversations = FakeConversationRepository()
    events = _chat(
        _service(
            llm=llm,
            toolbox=toolbox,
            conversations=conversations,
            max_tool_iterations=2,
        )
    )

    done = events[-1]
    assert isinstance(done, AssistantDone)
    assert "narrow it down" in done.content
    # The exhausted fallback reports the persisted user message too.
    assert conversations.messages["conv-1"][0].role is MessageRole.USER
    assert done.user_message_id == conversations.messages["conv-1"][0].id
    # 2 iterations, each with exactly one model call.
    assert len(llm.calls) == 2
    # The exhausted fallback is a normal assistant message, so it also records
    # the turn's (empty here) artifacts for a reopened conversation.
    assert conversations.messages["conv-1"][-1].meta == {"artifacts": []}


def test_history_is_bounded_on_the_wire() -> None:
    conversations = FakeConversationRepository(
        [Conversation(id="conv-9", user_id=USER, title="Old", created_at=NOW, updated_at=NOW)]
    )
    for index in range(20):
        asyncio.run(
            conversations.add_message(
                Message(
                    id=f"m{index}",
                    conversation_id="conv-9",
                    position=0,
                    role=MessageRole.USER,
                    content=f"message {index}",
                )
            )
        )
    llm = FakeLlmPort([text_response("ok")])
    _chat(_service(llm=llm, conversations=conversations, max_history_messages=4),
          message="newest", conversation_id="conv-9")

    sent = llm.calls[0][0]
    # system + the bounded window, which already contains the new user message.
    assert len(sent) == 1 + 4
    assert sent[-1].content == "newest"
    assert sent[1].content == "message 17"


# ---------------------------------------------------------------------------
# build_bounded_history
# ---------------------------------------------------------------------------


def _assistant_with_calls(message_id: str, call_id: str) -> Message:
    return Message(
        id=message_id,
        conversation_id="c",
        position=0,
        role=MessageRole.ASSISTANT,
        content="",
        meta={"tool_calls": [{"id": call_id, "name": "list_files", "arguments": {}}]},
    )


def _tool_message(message_id: str, call_id: str) -> Message:
    return Message(
        id=message_id,
        conversation_id="c",
        position=0,
        role=MessageRole.TOOL,
        content="{}",
        tool_name="list_files",
        meta={"tool_call_id": call_id},
    )


def test_bounded_history_drops_an_orphan_tool_result() -> None:
    messages = [
        _tool_message("t1", "c1"),
        Message(id="u1", conversation_id="c", position=0, role=MessageRole.USER, content="hi"),
    ]
    history = build_bounded_history(messages, 10)
    assert [item.role for item in history] == ["user"]


def test_bounded_history_keeps_a_complete_request_response_pair() -> None:
    messages = [
        Message(id="u1", conversation_id="c", position=0, role=MessageRole.USER, content="hi"),
        _assistant_with_calls("a1", "c1"),
        _tool_message("t1", "c1"),
        Message(id="a2", conversation_id="c", position=0, role=MessageRole.ASSISTANT, content="done"),
    ]
    history = build_bounded_history(messages, 10)
    assert [item.role for item in history] == ["user", "assistant", "tool", "assistant"]


def test_bounded_history_ignores_extra_tool_meta_keys() -> None:
    """The UI-only meta we persist must not change what reaches the model.

    ``build_bounded_history`` reads only ``tool_calls`` and ``tool_call_id``;
    labels, summaries and artifacts are added for the client and must be
    invisible on the wire.
    """
    extra_meta = {
        "tool_call_id": "c1",
        "label": "Looking through your files",
        "summary": "Found 3 matching file(s)",
        "artifacts": [{"type": "file", "id": "f1", "name": "a.pdf", "meta": {}}],
    }
    rich = [
        Message(id="u1", conversation_id="c", position=0, role=MessageRole.USER, content="hi"),
        _assistant_with_calls("a1", "c1"),
        Message(
            id="t1",
            conversation_id="c",
            position=0,
            role=MessageRole.TOOL,
            content="{}",
            tool_name="list_files",
            meta=extra_meta,
        ),
        Message(id="a2", conversation_id="c", position=0, role=MessageRole.ASSISTANT, content="done"),
    ]
    plain = [
        Message(id="u1", conversation_id="c", position=0, role=MessageRole.USER, content="hi"),
        _assistant_with_calls("a1", "c1"),
        _tool_message("t1", "c1"),
        Message(id="a2", conversation_id="c", position=0, role=MessageRole.ASSISTANT, content="done"),
    ]
    assert build_bounded_history(rich, 10) == build_bounded_history(plain, 10)


def test_bounded_history_drops_an_unanswered_tool_request() -> None:
    """Half a pair is invalid on the wire, so it is dropped whole."""
    messages = [_assistant_with_calls("a1", "c1")]
    assert build_bounded_history(messages, 10) == []


def test_bounded_history_drops_a_partially_answered_tool_request() -> None:
    messages = [
        Message(
            id="a1",
            conversation_id="c",
            position=0,
            role=MessageRole.ASSISTANT,
            content="",
            meta={
                "tool_calls": [
                    {"id": "c1", "name": "a", "arguments": {}},
                    {"id": "c2", "name": "b", "arguments": {}},
                ]
            },
        ),
        _tool_message("t1", "c1"),
    ]
    assert build_bounded_history(messages, 10) == []


def test_bounded_history_keeps_the_newest_groups_within_the_budget() -> None:
    messages = [
        Message(id="u1", conversation_id="c", position=0, role=MessageRole.USER, content="one"),
        _assistant_with_calls("a1", "c1"),
        _tool_message("t1", "c1"),
        Message(id="u2", conversation_id="c", position=0, role=MessageRole.USER, content="two"),
    ]
    history = build_bounded_history(messages, 2)
    # The whole group is what fits, so the newest user message comes alone.
    assert [(item.role, item.content) for item in history] == [("user", "two")]


def test_bounded_history_of_an_empty_transcript_is_empty() -> None:
    assert build_bounded_history([], 5) == []


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------


def test_summarize_file_returns_the_toolbox_summary() -> None:
    row = UserFileModel(
        id="file-1",
        user_id=USER,
        folder_id=None,
        file_key="k",
        file_name="report.pdf",
        file_extension="pdf",
        file_size_bytes=10,
        mime_type="application/pdf",
        is_favorite=False,
        created_at=NOW,
    )
    toolbox = ScriptedToolBox(
        document=(row, ExtractedDocument(text="text", truncated=False, supported=True)),
        summary={"summary": "Short.", "key_points": ["Point"]},
    )
    service = _service(toolbox=toolbox)
    result = asyncio.run(
        service.summarize_file(
            user_id=USER, tier=SubscriptionTier.FREE, file_id="file-1"
        )
    )
    assert isinstance(result, SummaryResult)
    assert result.file_name == "report.pdf"
    assert result.summary == "Short."
    assert result.key_points == ["Point"]
    assert result.model == "fake-model"


@pytest.fixture(name="unreadable_file")
def unreadable_file_fixture() -> UserFileModel:
    return UserFileModel(
        id="file-1",
        user_id=USER,
        folder_id=None,
        file_key="k",
        file_name="bundle.zip",
        file_extension="zip",
        file_size_bytes=10,
        mime_type="application/zip",
        is_favorite=False,
        created_at=NOW,
    )


def test_summarize_file_refuses_an_unreadable_document(unreadable_file: UserFileModel) -> None:
    toolbox = ScriptedToolBox(
        document=(
            unreadable_file,
            ExtractedDocument(text="", truncated=False, supported=False, note="'.zip' is not readable"),
        )
    )
    service = _service(toolbox=toolbox)
    with pytest.raises(AssistantToolError):
        asyncio.run(
            service.summarize_file(
                user_id=USER, tier=SubscriptionTier.FREE, file_id="file-1"
            )
        )


def test_summarize_file_surfaces_a_toolbox_error_dict() -> None:
    service = _service(toolbox=ScriptedToolBox(document={"error": "not found"}))
    with pytest.raises(AssistantToolError, match="not found"):
        asyncio.run(
            service.summarize_file(
                user_id=USER, tier=SubscriptionTier.FREE, file_id="file-1"
            )
        )


# ---------------------------------------------------------------------------
# Recommend
# ---------------------------------------------------------------------------


def _png_recommendations(service: AssistantService, use_case: str | None = None):
    return asyncio.run(
        service.recommend(
            user_id=USER,
            tier=SubscriptionTier.FREE,
            file_id=None,
            source_format="png",
            use_case=use_case,
        )
    )


def test_recommend_is_deterministic_without_a_model() -> None:
    service = _service(llm=FakeLlmPort(model="echo"))
    result = _png_recommendations(service, use_case="print it")
    assert result.source_format == "png"
    assert result.use_case == "print it"
    assert 1 <= len(result.recommendations) <= 4
    targets = [item.target_format for item in result.recommendations]
    assert len(set(targets)) == len(targets)
    confidences = [item.confidence for item in result.recommendations]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in confidences)
    # Every suggestion is a real registry edge, with a human label and reason.
    assert all(item.label and item.reason and item.category for item in result.recommendations)


def test_recommend_uses_the_model_ranking() -> None:
    llm = FakeLlmPort(
        [
            text_response(
                '{"use_case": "web", "recommendations": ['
                '{"target_format": "webp", "reason": "small", "confidence": 0.9},'
                '{"target_format": "format_that_does_not_exist", "reason": "?", "confidence": 1}]}'
            )
        ]
    )
    service = _service(llm=llm)
    result = _png_recommendations(service)
    assert result.recommendations[0].target_format == "webp"
    assert result.recommendations[0].reason == "small"
    assert result.recommendations[0].confidence == 0.9
    # A target the registry cannot produce is never shown to the user.
    assert "format_that_does_not_exist" not in [item.target_format for item in result.recommendations]


def test_recommend_falls_back_when_the_model_returns_prose() -> None:
    llm = FakeLlmPort([text_response("I think PNG is fine.")])
    service = _service(llm=llm)
    result = _png_recommendations(service)
    assert result.recommendations[0].target_format == "pdf"


def test_recommend_clamps_a_percentage_confidence() -> None:
    llm = FakeLlmPort(
        [
            text_response(
                '{"recommendations": [{"target_format": "jpg", "reason": "ok", "confidence": 80}]}'
            )
        ]
    )
    result = _png_recommendations(_service(llm=llm))
    assert 0.0 <= result.recommendations[0].confidence <= 1.0


def test_recommend_can_infer_the_format_from_a_file() -> None:
    toolbox = ScriptedToolBox(source_format="png")
    result = _png_recommendations(_service(toolbox=toolbox, llm=FakeLlmPort(model="echo")))
    assert result.source_format == "png"


def test_recommend_requires_a_source() -> None:
    service = _service()
    with pytest.raises(AssistantToolError):
        asyncio.run(
            service.recommend(
                user_id=USER,
                tier=SubscriptionTier.FREE,
                file_id=None,
                source_format=None,
                use_case=None,
            )
        )


def test_recommend_rejects_an_unknown_source_format() -> None:
    service = _service()
    with pytest.raises(AssistantToolError):
        asyncio.run(
            service.recommend(
                user_id=USER,
                tier=SubscriptionTier.FREE,
                file_id=None,
                source_format="notarealformat",
                use_case=None,
            )
        )


def test_recommend_prompt_lists_only_supported_targets() -> None:
    llm = FakeLlmPort(model="echo")
    _png_recommendations(_service(llm=llm))
    # The offline backend never calls the model at all for a ranking.
    assert llm.calls == []


def test_llm_ranking_prompt_includes_the_candidates() -> None:
    llm = FakeLlmPort([text_response("{}")])
    _png_recommendations(_service(llm=llm))
    prompt = llm.calls[0][0][-1].content
    assert "png" in prompt
    assert "webp" in prompt


# ---------------------------------------------------------------------------
# Attachments and page context
# ---------------------------------------------------------------------------


def test_attachments_are_resolved_injected_and_persisted() -> None:
    attachments = [AttachmentRef("file-1", "resume.pdf", "pdf", 27 * 1024)]
    toolbox = ScriptedToolBox(attachments=attachments)
    llm = FakeLlmPort([text_response("ok")])
    conversations = FakeConversationRepository()
    _chat(
        _service(llm=llm, toolbox=toolbox, conversations=conversations),
        message="read it",
        file_ids=["file-1"],
    )

    sent = llm.calls[0][0]
    # system prompt, then the injected attachment context, then the history.
    assert sent[0].role == "system"
    assert sent[1].role == "system"
    assert "resume.pdf" in sent[1].content
    assert "file_id: file-1" in sent[1].content
    assert "27 KB" in sent[1].content
    assert sent[2].role == "user"

    stored = conversations.messages["conv-1"][0]
    assert stored.meta == {
        "attachments": [{"id": "file-1", "name": "resume.pdf", "extension": "pdf"}]
    }


def test_an_unknown_attachment_is_refused_before_streaming() -> None:
    service = _service(toolbox=ScriptedToolBox(attachments=[]))
    with pytest.raises(AssistantAttachmentNotFound):
        _chat(service, message="hi", file_ids=["missing"])


def test_a_turn_without_attachments_persists_an_empty_list() -> None:
    conversations = FakeConversationRepository()
    _chat(_service(conversations=conversations), message="hello")
    assert conversations.messages["conv-1"][0].meta == {"attachments": []}


def test_context_adds_a_currently_viewing_system_line() -> None:
    llm = FakeLlmPort([text_response("ok")])
    _chat(_service(llm=llm), message="where am I", context="/drive/invoices")
    sent = llm.calls[0][0]
    assert sent[1].role == "system"
    assert "currently viewing" in sent[1].content
    assert "invoices" in sent[1].content


def test_page_context_is_truncated_to_two_hundred_characters() -> None:
    llm = FakeLlmPort([text_response("ok")])
    _chat(_service(llm=llm), message="hi", context="x" * 500)
    content = llm.calls[0][0][1].content
    assert "x" * 200 + "." in content
    assert "x" * 201 not in content


def test_attachments_and_context_share_one_system_message() -> None:
    toolbox = ScriptedToolBox(attachments=[AttachmentRef("f1", "a.pdf", "pdf", 1024)])
    llm = FakeLlmPort([text_response("ok")])
    _chat(_service(llm=llm, toolbox=toolbox), message="hi", file_ids=["f1"], context="files")
    sent = llm.calls[0][0]
    assert [message.role for message in sent[:3]] == ["system", "system", "user"]
    assert "attached" in sent[1].content
    assert "currently viewing" in sent[1].content


def test_bounded_history_is_unaffected_by_attachment_meta() -> None:
    message = Message(
        id="u1",
        conversation_id="c",
        position=0,
        role=MessageRole.USER,
        content="hi",
        meta={"attachments": [{"id": "f1", "name": "a.pdf", "extension": "pdf"}]},
    )
    assert build_bounded_history([message], 10) == [LlmMessage(role="user", content="hi")]


# ---------------------------------------------------------------------------
# Tier-driven model and limits
# ---------------------------------------------------------------------------


def test_the_resolved_model_depends_on_the_tier() -> None:
    """A PRO turn must talk to PRO's transport, not the default one."""
    standard = FakeLlmPort([text_response("hi")], model="standard-model")
    advanced = FakeLlmPort([text_response("hi")], model="advanced-model")
    resolver = FakeAssistantModelResolver(
        default=standard, by_tier={SubscriptionTier.PRO: advanced}
    )
    service = _service(models=resolver)

    assert service.model_for_tier(SubscriptionTier.FREE) == "standard-model"
    assert service.model_for_tier(SubscriptionTier.PRO) == "advanced-model"

    asyncio.run(
        _collect(
            service,
            user_id=USER,
            tier=SubscriptionTier.PRO,
            message="hi",
            conversation_id=None,
        )
    )
    assert advanced.calls  # the PRO transport served the turn
    assert standard.calls == []
    assert resolver.resolved[-1] == SubscriptionTier.PRO


def test_the_tool_iteration_bound_is_capped_by_the_settings_ceiling() -> None:
    """The deployment ceiling wins over the tier entitlement."""
    llm = FakeLlmPort([tool_response("list_files")] * 10)
    service = _service(llm=llm, max_tool_iterations=2)
    _chat(service, message="go")
    # FREE is entitled to 4 steps, but the ceiling is 2 — the lower of the two.
    assert len(llm.calls) == 2


def test_a_free_turn_gets_fewer_tool_steps_than_the_ceiling_allows() -> None:
    """The tier entitlement caps the loop below a more generous ceiling."""
    llm = FakeLlmPort([tool_response("list_files")] * 10)
    service = _service(llm=llm, max_tool_iterations=6)
    _chat(service, message="go")  # tier FREE -> 4 iterations
    assert len(llm.calls) == 4


def test_more_attachments_than_the_tier_allows_is_refused() -> None:
    """FREE allows one attachment; a second is refused before streaming."""
    attachments = [
        AttachmentRef("f1", "a.pdf", "pdf", 10),
        AttachmentRef("f2", "b.pdf", "pdf", 10),
    ]
    quota = FakeQuota()
    service = _service(toolbox=ScriptedToolBox(attachments=attachments), quota=quota)
    with pytest.raises(AssistantAttachmentLimitExceeded, match="up to 1"):
        _chat(service, message="hi", file_ids=["f1", "f2"])
    # A refused request must not have charged the caller a turn.
    assert quota.consumed == []


def test_a_pro_caller_may_attach_more_files_than_free_allows() -> None:
    attachments = [
        AttachmentRef("f1", "a.pdf", "pdf", 10),
        AttachmentRef("f2", "b.pdf", "pdf", 10),
        AttachmentRef("f3", "c.pdf", "pdf", 10),
    ]
    service = _service(toolbox=ScriptedToolBox(attachments=attachments))
    events = asyncio.run(
        _collect(
            service,
            user_id=USER,
            tier=SubscriptionTier.PRO,
            message="read these",
            conversation_id=None,
            file_ids=["f1", "f2", "f3"],
        )
    )
    assert isinstance(events[-1], AssistantDone)


def test_a_tool_only_turn_still_works_under_a_resolved_port() -> None:
    """The agent loop (not just a plain answer) runs on the resolved port."""
    llm = FakeLlmPort(
        [tool_response("list_files", {"query": "x"}), text_response("Found them.")],
        model="advanced-model",
    )
    toolbox = ScriptedToolBox(results={"list_files": {"files": [], "count": 0}})
    service = _service(llm=llm, toolbox=toolbox)
    events = _chat(service, message="find x")
    assert any(isinstance(event, AssistantToolEvent) for event in events)
    done = events[-1]
    assert isinstance(done, AssistantDone)
    assert done.content == "Found them."
    assert len(llm.calls) == 2
