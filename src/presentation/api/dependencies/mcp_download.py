"""Composition for MCP download capabilities, including outside FastAPI."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.mcp.download_capability import MCPDownloadCapabilities
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session, get_session_factory
from src.presentation.api.dependencies.service_dependencies import get_file_service


def build_mcp_download_capabilities(session: AsyncSession) -> MCPDownloadCapabilities:
    settings = get_settings()
    files = SQLUserFileRepository(session)
    folders = SQLUserFolderRepository(session)
    # FileService owns the folder subtree rule. Its storage and subscription
    # dependencies are not used by resolve_folder_scope.
    from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
    from src.presentation.api.dependencies.service_dependencies import get_minio_url_storage

    file_service = get_file_service(files, folders, get_minio_url_storage(), SQLSubscriptionRepository(session))
    return MCPDownloadCapabilities(
        repository=SQLMCPRepository(session),
        conversions=SQLConversionJobRepository(session),
        files=files,
        folders=file_service,
        secret=settings.SECRET_KEY.get_secret_value(),
        resource_url=settings.mcp_resource_url(),
    )


def get_mcp_download_capabilities(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MCPDownloadCapabilities:
    return build_mcp_download_capabilities(session)


async def create_mcp_download_link(access_token: str, job_id: str) -> dict:
    """Issue a link outside FastAPI, using a fresh, request-lifetime session."""
    async with get_session_factory()() as session:
        return await build_mcp_download_capabilities(session).create_link(access_token, job_id)
