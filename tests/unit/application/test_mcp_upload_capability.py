"""MCPUploadCapabilities: single use, fail-closed rechecks, folder confinement.

These tests exist because the capability is a bearer credential: anyone who has
it can perform one upload. The properties pinned here are the ones that make
that acceptable — tamper and expiry are refused, redemption is exactly once
(including under concurrency), and every redemption re-reads the grant so a
revoked, paused, narrowed or re-bound connection fails closed with the same
generic sentence.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.application.exceptions.file_system_exceptions import (
    FileRecordNotFoundError,
    FileSizeLimitExceededError,
)
from src.application.services.mcp.toolbox import MCPToolBox, MCPToolContext
from src.application.services.mcp.upload_capability import (
    MCPUploadCapabilities,
    UploadUnavailable,
)
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.enitities.agent_grant import AgentGrant
from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntentStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess
from src.domain.security.value_object.agent_scope import AgentScope
from tests.fakes.fake_mcp_upload_intent_repo import FakeMCPUploadIntentRepository

SECRET = "unit-test-secret-key-that-is-long-enough"
MAX_ATTACHMENT = 100 * 1024 * 1024
TTL = 900


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def grant(**overrides: Any) -> AgentGrant:
    values: dict[str, Any] = {
        "id": "grant-1",
        "user_id": 7,
        "client_id": "client-1",
        "client_name": "Test Agent",
        "scopes": (
            AgentScope.DOCUMENTS_READ,
            AgentScope.DOCUMENTS_WRITE,
            AgentScope.DOCUMENTS_CONVERT,
        ),
        "folder_access": FolderAccess.ALL,
    }
    values.update(overrides)
    return AgentGrant(**values)


class FakeGrants:
    def __init__(self, stored: AgentGrant | None) -> None:
        self.stored = stored

    async def get_grant(self, grant_id: str) -> AgentGrant | None:
        return self.stored if self.stored is not None and self.stored.id == grant_id else None


class FakeFiles:
    def __init__(
        self,
        *,
        cap: int = 50 * 1024 * 1024,
        error: Exception | None = None,
        subtrees: dict[str, set[str]] | None = None,
        rows: dict[str, Any] | None = None,
    ) -> None:
        self.cap = cap
        self.error = error
        self.subtrees = subtrees or {}
        self.rows = rows or {}
        self.authorize_calls: list[tuple[int, int | None]] = []

    async def authorize_upload_size(self, user_id: int, declared_size: int | None) -> int:
        self.authorize_calls.append((user_id, declared_size))
        if self.error is not None:
            raise self.error
        return self.cap

    async def resolve_folder_scope(self, user_id: int, folder_id: str) -> set[str] | None:
        return self.subtrees.get(folder_id)

    async def get_file(self, user_id: int, file_id: str) -> Any:
        row = self.rows.get(file_id)
        if row is None or row.user_id != user_id:
            raise FileRecordNotFoundError()
        return row


class FakeConversions:
    def __init__(self, job: ConversionJob | None = None) -> None:
        self.job = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.job if self.job is not None and self.job.job_id == job_id else None


def toolbox(files: FakeFiles) -> MCPToolBox:
    """The real toolbox, so the folder rule under test is the production one."""
    return MCPToolBox(
        file_service=files,  # type: ignore[arg-type]
        conversion_service=object(),  # type: ignore[arg-type]
        transfer_service=object(),  # type: ignore[arg-type]
        copier=object(),  # type: ignore[arg-type]
        credits=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        attachment_max_bytes=MAX_ATTACHMENT,
    )


def context(
    *,
    scopes: tuple[AgentScope, ...] | None = None,
    folder_access: FolderAccess = FolderAccess.ALL,
    folder_id: str | None = None,
    grant_id: str = "grant-1",
    user_id: int = 7,
) -> MCPToolContext:
    return MCPToolContext(
        user_id=user_id,
        scopes=scopes
        or (
            AgentScope.DOCUMENTS_READ,
            AgentScope.DOCUMENTS_WRITE,
            AgentScope.DOCUMENTS_CONVERT,
        ),
        client_id="client-1",
        grant_id=grant_id,
        folder_access=folder_access,
        folder_id=folder_id,
    )


def build(
    *,
    stored_grant: AgentGrant | None = None,
    files: FakeFiles | None = None,
    intents: FakeMCPUploadIntentRepository | None = None,
    conversions: FakeConversions | None = None,
    start_conversion: Any = None,
    clock: Clock | None = None,
) -> tuple[MCPUploadCapabilities, FakeMCPUploadIntentRepository, Clock]:
    clock = clock or Clock()
    intents = intents or FakeMCPUploadIntentRepository()
    files = files or FakeFiles()
    service = MCPUploadCapabilities(
        intents=intents,
        grants=FakeGrants(stored_grant if stored_grant is not None else grant()),
        files=files,
        conversions=conversions or FakeConversions(),
        documents=toolbox(files),
        secret=SECRET,
        resource_url="https://api.example.test/mcp",
        max_attachment_bytes=MAX_ATTACHMENT,
        ttl_seconds=TTL,
        start_conversion=start_conversion,
        clock=clock,
    )
    return service, intents, clock


async def _create(service: MCPUploadCapabilities, ctx: MCPToolContext | None = None, **kwargs: Any):
    return await service.create_intent(ctx or context(), file_name="report.pdf", **kwargs)


def test_create_returns_a_capability_and_a_pending_intent() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        created = await _create(service, size_bytes=1024)

        assert not isinstance(created, dict)
        assert created.upload_url == "https://api.example.test/api/v1/mcp/upload"
        assert created.header_name == "X-Transform-Upload"
        assert created.curl_command.startswith("curl --fail -sS -X PUT ")
        assert "--data-binary @'<path to file>'" in created.curl_command
        assert created.upload_page_url.endswith("#" + created.capability)
        assert created.intent.status is MCPUploadIntentStatus.PENDING
        assert created.intent.max_bytes == min(50 * 1024 * 1024, MAX_ATTACHMENT)
        # Nothing about the capability is guessable from the id.
        assert created.capability != created.intent.id

    asyncio.run(run())


def test_a_tampered_capability_is_refused() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        tampered = created.capability[:-1] + ("A" if created.capability[-1] != "A" else "B")
        with pytest.raises(UploadUnavailable):
            await service.redeem_start(tampered)

    asyncio.run(run())


def test_an_expired_link_is_refused() -> None:
    async def run() -> None:
        service, _intents, clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        clock.advance(TTL + 1)
        with pytest.raises(UploadUnavailable):
            await service.redeem_start(created.capability)

    asyncio.run(run())


def test_an_expired_intent_is_refused_even_with_a_fresh_token() -> None:
    async def run() -> None:
        service, intents, clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        stored = intents.intents[created.intent.id]
        stored.expires_at = datetime.fromtimestamp(clock.now, UTC) - timedelta(seconds=1)
        with pytest.raises(UploadUnavailable):
            await service.redeem_start(created.capability)

    asyncio.run(run())


def test_redeeming_twice_is_refused() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        first = await service.redeem_start(created.capability)
        assert first.status is MCPUploadIntentStatus.RECEIVING
        with pytest.raises(UploadUnavailable):
            await service.redeem_start(created.capability)

    asyncio.run(run())


def test_two_concurrent_redemptions_have_exactly_one_winner() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)

        results = await asyncio.gather(
            service.redeem_start(created.capability),
            service.redeem_start(created.capability),
            return_exceptions=True,
        )
        winners = [item for item in results if not isinstance(item, Exception)]
        losers = [item for item in results if isinstance(item, UploadUnavailable)]
        assert len(winners) == 1
        assert len(losers) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "REVOKED"},
        {"status": "PAUSED"},
        # A re-consent that removed documents.write must kill a pending upload.
        {"scopes": (AgentScope.DOCUMENTS_READ,)},
        # A re-bind to a folder that does not contain the destination must too.
        {"folder_access": FolderAccess.FOLDER, "folder_id": "other"},
    ],
)
def test_a_changed_grant_refuses_a_pending_upload(overrides: dict[str, Any]) -> None:
    async def run() -> None:
        files = FakeFiles(subtrees={"bound": {"bound", "child"}, "other": {"other"}})
        service, _intents, _clock = build(
            stored_grant=grant(folder_access=FolderAccess.FOLDER, folder_id="bound"),
            files=files,
        )
        created = await _create(service)
        assert not isinstance(created, dict)

        # The grant changes between minting and redemption.
        service._grants.stored = grant(**overrides)  # type: ignore[attr-defined]
        with pytest.raises(UploadUnavailable):
            await service.redeem_start(created.capability)

    asyncio.run(run())


def test_a_confined_grant_defaults_to_its_bound_folder() -> None:
    async def run() -> None:
        files = FakeFiles(subtrees={"bound": {"bound", "child"}})
        service, _intents, _clock = build(
            stored_grant=grant(folder_access=FolderAccess.FOLDER, folder_id="bound"),
            files=files,
        )
        ctx = context(folder_access=FolderAccess.FOLDER, folder_id="bound")
        created = await _create(service, ctx)
        assert not isinstance(created, dict)
        assert created.intent.folder_id == "bound"

        child = await _create(service, ctx, folder_id="child")
        assert not isinstance(child, dict)
        assert child.intent.folder_id == "child"

    asyncio.run(run())


def test_a_folder_outside_the_subtree_is_refused() -> None:
    async def run() -> None:
        files = FakeFiles(subtrees={"bound": {"bound", "child"}})
        service, _intents, _clock = build(
            stored_grant=grant(folder_access=FolderAccess.FOLDER, folder_id="bound"),
            files=files,
        )
        ctx = context(folder_access=FolderAccess.FOLDER, folder_id="bound")
        refused = await _create(service, ctx, folder_id="outside")
        assert isinstance(refused, dict)
        assert refused["ok"] is False
        assert refused["error"] == "That folder is not available to this connection."

    asyncio.run(run())


def test_an_unrestricted_grant_defaults_to_the_drive_root() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        assert created.intent.folder_id is None

    asyncio.run(run())


def test_the_tier_cap_message_is_passed_through() -> None:
    async def run() -> None:
        files = FakeFiles(
            error=FileSizeLimitExceededError(
                "File exceeds the maximum size for your tier (10 MB)."
            )
        )
        service, _intents, _clock = build(files=files)
        refused = await _create(service, size_bytes=11 * 1024 * 1024)
        assert isinstance(refused, dict)
        assert refused["ok"] is False
        assert "maximum size for your tier" in refused["error"]

    asyncio.run(run())


def test_a_size_above_the_mcp_ceiling_is_refused() -> None:
    async def run() -> None:
        service, _intents, _clock = build(files=FakeFiles(cap=5 * 1024 * 1024 * 1024))
        refused = await _create(service, size_bytes=MAX_ATTACHMENT + 1)
        assert isinstance(refused, dict)
        assert refused["ok"] is False
        assert "100 MB" in refused["error"]

    asyncio.run(run())


def test_an_unsupported_target_format_is_refused_with_the_alternatives() -> None:
    async def run() -> None:
        service, _intents, _clock = build()
        refused = await _create(service, target_format="not-a-format")
        assert isinstance(refused, dict)
        assert refused["ok"] is False
        assert refused["source_format"] == "pdf"
        assert "docx" in refused["target_formats"]

    asyncio.run(run())


def test_complete_starts_the_conversion_through_the_supplied_path() -> None:
    calls: list[tuple[str, str | None]] = []

    async def start(intent: Any) -> dict[str, Any]:
        calls.append((intent.file_id, intent.target_format))
        return {"ok": True, "job": {"job_id": "job-9"}}

    async def run() -> None:
        service, _intents, _clock = build(start_conversion=start)
        created = await _create(service, target_format="docx")
        assert not isinstance(created, dict)
        completed = await service.complete(created.intent, "file-1")
        assert completed.status is MCPUploadIntentStatus.COMPLETED
        assert completed.job_id == "job-9"
        assert calls == [("file-1", "docx")]

    asyncio.run(run())


def test_a_failed_conversion_keeps_the_file_and_reports_the_failure() -> None:
    async def start(_intent: Any) -> dict[str, Any]:
        return {"ok": False, "error": "Conversion is unavailable right now."}

    async def run() -> None:
        service, intents, _clock = build(start_conversion=start)
        from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntent

        intent = await intents.create(
            MCPUploadIntent(
                id="intent-2",
                user_id=7,
                grant_id="grant-1",
                client_id="client-1",
                file_name="report.pdf",
                extension="pdf",
                max_bytes=MAX_ATTACHMENT,
                target_format="png",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(seconds=TTL),
            )
        )
        completed = await service.complete(intent, "file-2")
        assert completed.status is MCPUploadIntentStatus.FAILED
        assert completed.file_id == "file-2"
        assert completed.error == "Conversion is unavailable right now."

    asyncio.run(run())


def test_status_is_scoped_to_the_owner_and_grant() -> None:
    async def run() -> None:
        files = FakeFiles()
        service, _intents, _clock = build(files=files)
        created = await _create(service)
        assert not isinstance(created, dict)

        other_grant = await service.get_status(
            context(grant_id="another-grant"), created.intent.id
        )
        assert other_grant == {"ok": False, "error": "No such upload was found."}

        other_user = await service.get_status(
            context(user_id=8), created.intent.id
        )
        assert other_user == {"ok": False, "error": "No such upload was found."}

        missing = await service.get_status(context(), "nope")
        assert missing == {"ok": False, "error": "No such upload was found."}

    asyncio.run(run())


def test_status_returns_the_file_and_job_without_storage_details() -> None:
    async def run() -> None:
        created_at = datetime.now(UTC)
        files = FakeFiles(
            rows={
                "file-1": SimpleNamespace(
                    id="file-1",
                    user_id=7,
                    file_name="report.pdf",
                    file_extension="pdf",
                    file_size_bytes=10,
                    folder_id=None,
                    created_at=created_at,
                    file_key="upload/secret-key",
                )
            }
        )
        job = ConversionJob(
            job_id="job-1",
            conversion=ConversionType(source_format="pdf", target_format="docx"),
            input_file="report.pdf",
            object_key="upload/secret-key",
            user_id=7,
            status=JobStatus.COMPLETED,
            output_file="output/report.docx",
        )
        service, intents, _clock = build(files=files, conversions=FakeConversions(job))
        from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntent

        intent = await intents.create(
            MCPUploadIntent(
                id="intent-3",
                user_id=7,
                grant_id="grant-1",
                client_id="client-1",
                file_name="report.pdf",
                extension="pdf",
                max_bytes=MAX_ATTACHMENT,
                target_format="docx",
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=TTL),
            )
        )
        await intents.mark_completed(intent.id, file_id="file-1", job_id="job-1")

        result = await service.get_status(context(), intent.id)
        assert result["ok"] is True
        assert result["status"] == "COMPLETED"
        assert result["file"]["file_id"] == "file-1"
        assert result["job"]["job_id"] == "job-1"
        serialized = str(result)
        assert "secret-key" not in serialized
        assert "object_key" not in serialized
        assert "file_key" not in serialized

    asyncio.run(run())


def test_the_capability_is_never_logged_only_its_fingerprint() -> None:
    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            # The audit helper passes its structured fields as ``args``; the
            # JSON formatter is what renders them, and caplog (propagate=False)
            # never sees the handler output.
            self.messages.append(record.getMessage() + " " + str(record.args))

    async def run(capture: Capture) -> str:
        service, _intents, _clock = build()
        created = await _create(service)
        assert not isinstance(created, dict)
        return created.capability

    logger = logging.getLogger("transform.security_audit")
    capture = Capture()
    logger.addHandler(capture)
    try:
        capability = asyncio.run(run(capture))
    finally:
        logger.removeHandler(capture)

    joined = "\n".join(capture.messages)
    assert capability not in joined
    assert MCPUploadCapabilities.fingerprint(capability) in joined
