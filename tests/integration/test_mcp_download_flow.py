"""Real SQLite repositories and HTTP redemption for MCP download links."""

import asyncio
import io
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import FastAPI
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.application.services.mcp.access_service import hash_secret
from src.application.services.mcp.download_capability import DownloadUnavailable
from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import (
    ConversionJobModel, MCPAgentGrantModel, MCPTokenModel, UserFileModel, UserFolderModel,
)
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.mcp_download import build_mcp_download_capabilities
from src.presentation.api.dependencies.service_dependencies import get_encryption_service, get_minio_download_adapter
from src.presentation.api.routers.v1.mcp_download import router


class Storage:
    def get_object_stream(self, key):
        assert key == "output/document.pdf"
        return io.BytesIO(b"converted")


def test_real_repositories_enforce_folder_changes_and_revocation(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'download.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                UserFolderModel(id="bound", user_id=7, name="bound"),
                UserFolderModel(id="child", user_id=7, parent_id="bound", name="child"),
                UserFolderModel(id="outside", user_id=7, name="outside"),
                UserFileModel(id="file", user_id=7, folder_id="child", file_key="input/key",
                              file_name="doc.docx", file_extension="docx", file_size_bytes=8,
                              mime_type="application/octet-stream"),
                ConversionJobModel(job_id="job", user_id=7, status=JobStatus.COMPLETED,
                                   source_format="docx", target_format="pdf", input_file="doc.docx",
                                   object_key="input/key", output_file="output/document.pdf"),
                MCPAgentGrantModel(id="grant", user_id=7, client_id="client", client_name="client",
                                   scopes="documents.read", status="ACTIVE", folder_access="FOLDER",
                                   folder_id="bound"),
                MCPTokenModel(token_hash=hash_secret("oauth-token"), grant_id="grant",
                              client_id="client", subject="7", kind="ACCESS",
                              scopes="documents.read", resource=get_settings().mcp_resource_url(),
                              expires_at=datetime.now(UTC) + timedelta(hours=1)),
            ])
            await db.commit()

    async def issue():
        async with factory() as db:
            return await build_mcp_download_capabilities(db).create_link("oauth-token", "job")

    async def set_folder(folder):
        async with factory() as db:
            row = await db.get(UserFileModel, "file")
            assert row is not None
            row.folder_id = folder
            await db.commit()

    async def revoke():
        async with factory() as db:
            grant = await db.get(MCPAgentGrantModel, "grant")
            assert grant is not None
            grant.status = "REVOKED"
            await db.commit()

    async def override_db():
        async with factory() as db:
            yield db

    asyncio.run(seed())
    capability = urlsplit(asyncio.run(issue())["download_url"]).fragment
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_minio_download_adapter] = lambda: Storage()
    app.dependency_overrides[get_encryption_service] = lambda: None
    try:
        with TestClient(app) as c:
            assert c.post("/api/v1/mcp/download/redeem", data={"capability": capability}).content == b"converted"
            asyncio.run(set_folder("outside"))
            assert c.post("/api/v1/mcp/download/redeem", data={"capability": capability}).status_code == 404
            with pytest.raises(DownloadUnavailable):
                asyncio.run(issue())
            asyncio.run(set_folder("child"))
            asyncio.run(revoke())
            assert c.post("/api/v1/mcp/download/redeem", data={"capability": capability}).status_code == 404
            with pytest.raises(DownloadUnavailable):
                asyncio.run(issue())
    finally:
        asyncio.run(engine.dispose())
