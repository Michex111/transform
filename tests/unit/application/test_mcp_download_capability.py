"""MCP download capabilities are scoped, revocable and short-lived."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from src.application.services.mcp.access_service import hash_secret
from src.application.services.mcp.download_capability import DownloadUnavailable, MCPDownloadCapabilities
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess
from src.domain.security.value_object.agent_scope import AgentScope

NOW = 1_800_000_000
RESOURCE = "https://api.example.test/mcp"


class Fixture:
    def __init__(self) -> None:
        self.now = NOW
        self.grant = AgentGrant(
            id="grant", user_id=7, client_id="client", client_name="client",
            scopes=(AgentScope.DOCUMENTS_READ,),
        )
        self.token = SimpleNamespace(
            kind="ACCESS", revoked_at=None,
            expires_at=datetime.fromtimestamp(NOW, UTC) + timedelta(hours=1),
            resource=RESOURCE, grant_id="grant", subject="7", client_id="client",
            scopes=(AgentScope.DOCUMENTS_READ,),
        )
        self.job = ConversionJob(
            job_id="job", conversion=ConversionType("docx", "pdf"),
            input_file="input.docx", object_key="input/key", output_file="output/key.pdf",
            user_id=7, status=JobStatus.COMPLETED,
        )
        self.row: SimpleNamespace | None = SimpleNamespace(folder_id="child")
        self.subtree = {"bound", "child"}
        self.service = MCPDownloadCapabilities(
            repository=self, conversions=self, files=self, folders=self,
            secret="test-secret", resource_url=RESOURCE, clock=lambda: self.now,
        )

    async def get_token(self, token_hash):
        return self.token if token_hash == hash_secret("real-token") else None

    async def get_grant(self, grant_id):
        return self.grant if grant_id == "grant" else None

    async def get_conversion_job(self, job_id):
        return self.job if job_id == "job" else None

    async def find_by_key(self, user_id, file_key):
        assert user_id == 7 and file_key == "input/key"
        return self.row

    async def resolve_folder_scope(self, user_id, folder_id):
        assert user_id == 7 and folder_id == "bound"
        return self.subtree

    async def issue(self):
        result = await self.service.create_link("real-token", "job")
        assert result["expires_in_seconds"] == 300
        parsed = urlsplit(result["download_url"])
        assert parsed.scheme == "https"
        assert not parsed.query and parsed.path == "/api/v1/mcp/download"
        assert "real-token" not in result["download_url"]
        return parsed.fragment


def test_link_authorizes_twice_and_expires() -> None:
    async def run():
        f = Fixture()
        token = await f.issue()
        target = await f.service.redeem(token)
        assert (target.user_id, target.output_key) == (7, "output/key.pdf")
        f.now += 301
        with pytest.raises(DownloadUnavailable):
            await f.service.redeem(token)
    asyncio.run(run())


def test_tampering_and_unrelated_token_are_denied() -> None:
    async def run():
        f = Fixture()
        token = await f.issue()
        with pytest.raises(DownloadUnavailable):
            await f.service.redeem(("A" if token[0] != "A" else "B") + token[1:])
        with pytest.raises(DownloadUnavailable):
            await f.service.create_link("other-token", "job")
    asyncio.run(run())


@pytest.mark.parametrize("change", ["revoked", "paused", "scope", "token_revoked", "expired", "output", "owner"])
def test_capability_is_reauthorized_on_redeem(change: str) -> None:
    async def run():
        f = Fixture()
        token = await f.issue()
        if change == "revoked":
            f.grant.status = AgentGrantStatus.REVOKED
        elif change == "paused":
            f.grant.status = AgentGrantStatus.PAUSED
        elif change == "scope":
            f.grant.scopes = (AgentScope.DOCUMENTS_CONVERT,)
        elif change == "token_revoked":
            f.token.revoked_at = datetime.fromtimestamp(NOW, UTC)
        elif change == "expired":
            f.token.expires_at = datetime.fromtimestamp(NOW, UTC)
        elif change == "output":
            f.job.output_file = "output/replaced.pdf"
        else:
            f.job.user_id = 8
        with pytest.raises(DownloadUnavailable):
            await f.service.redeem(token)
    asyncio.run(run())


def test_restricted_folder_checked_at_issue_and_redemption() -> None:
    async def run():
        f = Fixture()
        f.grant.folder_access = FolderAccess.FOLDER
        f.grant.folder_id = "bound"
        assert f.row is not None
        f.row.folder_id = "outside"
        with pytest.raises(DownloadUnavailable):
            await f.issue()
        f.row.folder_id = "child"
        token = await f.issue()
        f.row.folder_id = "outside"
        with pytest.raises(DownloadUnavailable):
            await f.service.redeem(token)
        f.row = None  # direct staging or deleted Drive source is not attributable
        with pytest.raises(DownloadUnavailable):
            await f.service.redeem(token)
        f.grant.folder_id = None
        with pytest.raises(DownloadUnavailable):
            await f.service.create_link("real-token", "job")
    asyncio.run(run())


@pytest.mark.parametrize("change", ["foreign", "missing", "pending", "no_output", "no_read"])
def test_cannot_issue_for_inaccessible_job(change: str) -> None:
    async def run():
        f = Fixture()
        if change == "foreign":
            f.job.user_id = 8
        elif change == "missing":
            f.job.job_id = "other"
            async def missing(job_id): return None
            f.service._conversions = SimpleNamespace(get_conversion_job=missing)
        elif change == "pending":
            f.job.status = JobStatus.PROCESSING
        elif change == "no_output":
            f.job.output_file = None
        else:
            f.token.scopes = (AgentScope.DOCUMENTS_CONVERT,)
        with pytest.raises(DownloadUnavailable):
            await f.service.create_link("real-token", "job")
    asyncio.run(run())
