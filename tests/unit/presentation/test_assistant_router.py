"""Unit tests for the assistant router: SSE framing and the HTTP error contract.

The frame vocabulary is the interface the SPA codes against, so it is asserted
literally (event name plus payload keys) rather than loosely. The interesting
HTTP behaviour is the access mapping: a tier with no allowance is a 403, a spent
quota is a 429, and a foreign conversation is the same 404 as a missing one.
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

import src.presentation.api.main as api_main
from src.application.dtos.assistant_dto import (
    Artifact,
    AssistantDone,
    AssistantError,
    AssistantTextDelta,
    AssistantToolEvent,
    DeletionOutcome,
)
from src.application.ports.llm_port import LlmUnavailableError
from src.application.services.assistant_service import AssistantConversationNotFound
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantAttachmentLimitExceeded,
    AssistantAttachmentNotFound,
    AssistantDeletionNotFound,
    AssistantDisabledError,
    AssistantQuotaExceeded,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.presentation.api.dependencies.service_dependencies import (
    get_assistant_model_registry,
    get_assistant_quota,
    get_assistant_service,
    get_conversation_repository,
    get_subscription_repository,
)
from src.presentation.api.routers.v1.assistant import _error_frame, _frame, _frames_for
from tests.fakes.fake_assistant_model_resolver import FakeAssistantModelResolver
from tests.fakes.fake_llm_port import FakeLlmPort, text_response
from tests.integration.dependencies.api_overrides import create_test_client

ARTIFACT = Artifact(type="file", id="file-1", name="report.pdf", meta={"extension": "pdf"})


def _parse(frame: str) -> tuple[str, dict[str, Any]]:
    """``("delta", {...})`` from a rendered SSE frame."""
    lines = frame.rstrip("\n").split("\n")
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")
    return lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def test_a_frame_is_one_event_and_one_json_line() -> None:
    assert _frame("status", {"stage": "thinking"}) == (
        'event: status\ndata: {"stage": "thinking"}\n\n'
    )


def test_a_text_delta_becomes_a_delta_frame() -> None:
    frames = list(_frames_for(AssistantTextDelta(text="Hi")))
    assert [_parse(frame) for frame in frames] == [("delta", {"text": "Hi"})]


def test_a_running_tool_event_has_no_summary_or_artifacts() -> None:
    frames = list(_frames_for(AssistantToolEvent(name="list_files", label="Reading", status="running")))
    assert [_parse(frame) for frame in frames] == [
        ("tool", {"name": "list_files", "label": "Reading", "status": "running"})
    ]


def test_a_finished_tool_event_carries_its_artifacts_and_emits_artifact_frames() -> None:
    frames = list(
        _frames_for(
            AssistantToolEvent(
                name="list_files",
                label="Reading",
                status="done",
                summary="Found 1 file",
                artifacts=(ARTIFACT,),
            )
        )
    )
    kind, payload = _parse(frames[0])
    assert kind == "tool"
    assert payload["summary"] == "Found 1 file"
    assert payload["artifacts"][0]["id"] == "file-1"

    kind, artifact_payload = _parse(frames[1])
    assert kind == "artifact"
    assert artifact_payload == {
        "type": "file",
        "id": "file-1",
        "name": "report.pdf",
        "meta": {"extension": "pdf"},
    }


def test_a_done_event_carries_the_conversation_and_full_text() -> None:
    frames = list(
        _frames_for(
            AssistantDone(
                conversation_id="conv-1",
                message_id="msg-1",
                content="All done",
                artifacts=(ARTIFACT,),
                user_message_id="user-1",
            )
        )
    )
    kind, payload = _parse(frames[0])
    assert kind == "done"
    assert payload["conversation_id"] == "conv-1"
    assert payload["message_id"] == "msg-1"
    assert payload["user_message_id"] == "user-1"
    assert payload["content"] == "All done"
    assert payload["artifacts"][0]["id"] == "file-1"


def test_an_error_event_becomes_an_error_frame() -> None:
    frames = list(_frames_for(AssistantError(code="QUOTA_EXCEEDED", message="Too many")))
    assert [_parse(frame) for frame in frames] == [
        ("error", {"code": "QUOTA_EXCEEDED", "message": "Too many"})
    ]


def test_error_frames_use_the_documented_codes() -> None:
    assert "QUOTA_EXCEEDED" in _error_frame(AssistantQuotaExceeded("x"))
    assert "AI_NOT_AVAILABLE_FOR_TIER" in _error_frame(AssistantDisabledError("x"))
    assert "NOT_FOUND" in _error_frame(AssistantConversationNotFound("x"))
    unknown = _error_frame(RuntimeError("boom"))
    assert "INTERNAL_ERROR" in unknown
    # Internal detail is never echoed to the client.
    assert "boom" not in unknown


def test_a_throttled_provider_is_reported_as_busy_not_broken() -> None:
    """A spent rate limit is a transient condition, not an internal error.

    "Try again shortly" is a materially different thing to tell someone who is
    waiting on a document conversion than "an unexpected error occurred", and
    the code lets the SPA treat it as retryable.
    """
    frame = _error_frame(LlmUnavailableError("429 rate limit ... org_abc ... upgrade"))

    code, payload = _parse(frame)
    assert code == "error"
    assert payload["code"] == "AI_BUSY"
    assert "try again" in payload["message"].lower()
    # The provider body names the upstream org and carries an upsell link; none
    # of that belongs in a user-facing message.
    assert "org_abc" not in frame
    assert "upgrade" not in frame.lower()


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------


class StubSubscriptionRepository:
    def __init__(self, tier: SubscriptionTier) -> None:
        self.tier = tier

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        del user_id
        return self.tier


class StubConversationRepository:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, int]] = []

    async def list_conversations(self, user_id: int, *, limit: int = 50) -> list:
        del user_id, limit
        return []

    async def get_conversation(self, conversation_id: str, user_id: int):
        del conversation_id, user_id
        return None

    async def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        self.deleted.append((conversation_id, user_id))
        return False


class StubQuota:
    """A quota whose consumed count is fixed, for the ``/status`` usage fields."""

    def __init__(self, used: int = 0) -> None:
        self.used = used
        self.consumed: list[tuple[int, int]] = []

    async def consume(self, user_id: int, limit: int) -> None:
        self.consumed.append((user_id, limit))

    async def peek(self, user_id: int) -> int:
        del user_id
        return self.used


class StubAssistantService:
    """Raises or streams exactly what a test asks it to."""

    def __init__(
        self,
        error: Exception | None = None,
        *,
        deletion_error: Exception | None = None,
    ) -> None:
        self.error = error
        self.deletion_error = deletion_error
        self.resolved: list[tuple[str, str, bool]] = []

    async def stream_chat(
        self,
        *,
        user_id,
        tier,
        message,
        conversation_id,
        file_ids=None,
        context=None,
        origin: JobOrigin = JobOrigin.WEB,
    ):
        del user_id, tier, message, conversation_id, file_ids, context, origin
        if self.error is not None:
            raise self.error
        yield AssistantTextDelta(text="Hello")
        yield AssistantDone(
            conversation_id="conv-1", message_id="msg-1", content="Hello"
        )

    async def resolve_deletion(
        self, *, user_id: int, conversation_id: str, file_id: str, approve: bool
    ) -> DeletionOutcome:
        del user_id
        self.resolved.append((conversation_id, file_id, approve))
        if self.deletion_error is not None:
            raise self.deletion_error
        return DeletionOutcome(
            file_id=file_id,
            file_name="report.pdf",
            state="deleted" if approve else "cancelled",
        )

    async def summarize_file(self, *, user_id: int, file_id: str):
        raise AssertionError("not used in this test")

    async def recommend(self, *, user_id: int, file_id, source_format, use_case):
        raise AssertionError("not used in this test")


@contextmanager
def _client(
    tier: SubscriptionTier,
    assistant: StubAssistantService,
    *,
    resolver: FakeAssistantModelResolver | None = None,
    quota: StubQuota | None = None,
) -> Generator[TestClient, None, None]:
    """A client with auth and every assistant dependency stubbed out.

    Overrides are registered on ``api_main.app`` (not on the client) because
    that is the typed path to the registry; ``create_test_client`` clears them on
    exit.
    """
    with create_test_client() as client:
        api_main.app.dependency_overrides[get_subscription_repository] = (
            lambda: StubSubscriptionRepository(tier)
        )
        api_main.app.dependency_overrides[get_assistant_service] = lambda: assistant
        api_main.app.dependency_overrides[get_assistant_model_registry] = lambda: (
            resolver
            or FakeAssistantModelResolver(
                default=FakeLlmPort([text_response("x")], model="test-model")
            )
        )
        api_main.app.dependency_overrides[get_assistant_quota] = (
            lambda: quota or StubQuota()
        )
        api_main.app.dependency_overrides[get_conversation_repository] = (
            StubConversationRepository
        )
        yield client


def test_status_reports_enabled_and_the_resolved_backend() -> None:
    with _client(SubscriptionTier.PRO, StubAssistantService()) as client:
        response = client.get("/api/v1/assistant/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    # conftest pins AI_BACKEND=echo; the model comes from the injected resolver.
    assert body["backend"] == "echo"
    assert body["model"] == "test-model"


def test_status_reports_the_tier_entitlements_and_usage() -> None:
    with _client(
        SubscriptionTier.PRO, StubAssistantService(), quota=StubQuota(used=5)
    ) as client:
        body = client.get("/api/v1/assistant/status").json()
    assert body["tier"] == "PRO"
    assert body["model_level"] == "advanced"
    assert body["model_label"] == "Advanced"
    assert body["requests_per_hour"] == 60
    assert body["used_this_hour"] == 5
    assert body["remaining_this_hour"] == 55
    assert body["max_attachments"] == 3
    assert body["max_actions_per_turn"] == 3
    assert body["max_document_bytes"] == 25 * 1024 * 1024


def test_status_clamps_remaining_usage_at_zero() -> None:
    with _client(
        SubscriptionTier.FREE, StubAssistantService(), quota=StubQuota(used=10_000)
    ) as client:
        body = client.get("/api/v1/assistant/status").json()
    assert body["used_this_hour"] == 10_000
    assert body["remaining_this_hour"] == 0


def test_status_resolves_the_model_for_the_callers_tier() -> None:
    resolver = FakeAssistantModelResolver(
        default=FakeLlmPort([text_response("x")], model="standard-model")
    )
    with _client(SubscriptionTier.PRO, StubAssistantService(), resolver=resolver) as client:
        body = client.get("/api/v1/assistant/status").json()
    assert resolver.resolved == [SubscriptionTier.PRO]
    assert body["model"] == "standard-model"


def test_status_reports_disabled_for_a_tier_without_allowance() -> None:
    with _client(SubscriptionTier.GUEST, StubAssistantService()) as client:
        response = client.get("/api/v1/assistant/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_chat_returns_403_when_the_tier_has_no_allowance() -> None:
    with _client(
        SubscriptionTier.FREE, StubAssistantService(AssistantDisabledError("nope"))
    ) as client:
        response = client.post("/api/v1/assistant/chat", json={"message": "hi"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AI_NOT_AVAILABLE_FOR_TIER"


def test_chat_returns_429_when_the_quota_is_spent() -> None:
    with _client(
        SubscriptionTier.FREE, StubAssistantService(AssistantQuotaExceeded("too many"))
    ) as client:
        response = client.post("/api/v1/assistant/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "QUOTA_EXCEEDED"


def test_chat_returns_404_for_a_conversation_the_caller_does_not_own() -> None:
    with _client(
        SubscriptionTier.FREE, StubAssistantService(AssistantConversationNotFound("gone"))
    ) as client:
        response = client.post(
            "/api/v1/assistant/chat", json={"message": "hi", "conversation_id": "conv-x"}
        )
    assert response.status_code == 404


def test_chat_returns_404_for_an_unowned_attachment() -> None:
    with _client(
        SubscriptionTier.FREE, StubAssistantService(AssistantAttachmentNotFound("gone"))
    ) as client:
        response = client.post(
            "/api/v1/assistant/chat", json={"message": "hi", "file_ids": ["file-x"]}
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ATTACHMENT_NOT_FOUND"


def test_chat_returns_403_when_too_many_attachments_are_sent() -> None:
    with _client(
        SubscriptionTier.FREE,
        StubAssistantService(
            AssistantAttachmentLimitExceeded(
                "Your plan allows up to 1 attachments per message."
            )
        ),
    ) as client:
        response = client.post(
            "/api/v1/assistant/chat",
            json={"message": "hi", "file_ids": ["file-a", "file-b"]},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ATTACHMENT_LIMIT_EXCEEDED"
    assert "up to 1 attachments" in response.json()["detail"]["message"]


def test_chat_rejects_more_than_five_attachments() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        response = client.post(
            "/api/v1/assistant/chat",
            json={"message": "hi", "file_ids": [f"file-{index}" for index in range(6)]},
        )
    assert response.status_code == 422


def test_chat_rejects_an_empty_message() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        response = client.post("/api/v1/assistant/chat", json={"message": ""})
    assert response.status_code == 422


def test_chat_streams_the_documented_frames() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        with client.stream(
            "POST", "/api/v1/assistant/chat", json={"message": "hi"}
        ) as stream:
            events = [
                line[len("event: ") :]
                for line in stream.iter_lines()
                if line.startswith("event: ")
            ]
    assert events == ["status", "delta", "done"]


def test_conversation_endpoints_use_the_repository_and_hide_foreign_ids() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        assert client.get("/api/v1/assistant/conversations").json() == {"conversations": []}
        assert client.get("/api/v1/assistant/conversations/conv-x").status_code == 404
        assert client.delete("/api/v1/assistant/conversations/conv-x").status_code == 404
        # The truncate route has one more path segment, so it does not shadow
        # the conversation routes; a foreign conversation is still a 404.
        assert client.delete(
            "/api/v1/assistant/conversations/conv-x/messages/msg-x"
        ).status_code == 404


def test_recommend_requires_a_file_or_a_format() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        response = client.post("/api/v1/assistant/recommend", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MISSING_SOURCE"


# ---------------------------------------------------------------------------
# Deletion confirmations
# ---------------------------------------------------------------------------


def test_resolve_deletion_returns_the_outcome_shape() -> None:
    stub = StubAssistantService()
    with _client(SubscriptionTier.FREE, stub) as client:
        response = client.post(
            "/api/v1/assistant/conversations/conv-1/deletions",
            json={"file_id": "file-1", "approve": True},
        )
    assert response.status_code == 200
    assert response.json() == {
        "file_id": "file-1",
        "file_name": "report.pdf",
        "state": "deleted",
    }
    assert stub.resolved == [("conv-1", "file-1", True)]


def test_resolve_deletion_cancel_reports_cancelled() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        response = client.post(
            "/api/v1/assistant/conversations/conv-1/deletions",
            json={"file_id": "file-1", "approve": False},
        )
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"


def test_resolve_deletion_without_a_live_proposal_is_404_deletion_not_found() -> None:
    with _client(
        SubscriptionTier.FREE,
        StubAssistantService(deletion_error=AssistantDeletionNotFound("stale")),
    ) as client:
        response = client.post(
            "/api/v1/assistant/conversations/conv-1/deletions",
            json={"file_id": "file-1", "approve": True},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DELETION_NOT_FOUND"


def test_resolve_deletion_of_an_unowned_conversation_is_404_deletion_not_found() -> None:
    with _client(
        SubscriptionTier.FREE,
        StubAssistantService(
            deletion_error=AssistantConversationNotFound("not yours")
        ),
    ) as client:
        response = client.post(
            "/api/v1/assistant/conversations/conv-x/deletions",
            json={"file_id": "file-1", "approve": True},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DELETION_NOT_FOUND"


def test_resolve_deletion_requires_a_file_id() -> None:
    with _client(SubscriptionTier.FREE, StubAssistantService()) as client:
        response = client.post(
            "/api/v1/assistant/conversations/conv-1/deletions", json={"approve": True}
        )
    assert response.status_code == 422
