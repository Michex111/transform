"""Process wiring for the MCP server.

An MCP request does **not** arrive through FastAPI, so the ``Depends``-based
composition in ``presentation/api/dependencies/service_dependencies.py`` cannot
build its services. This module provides the equivalent for that path.

Two deliberate properties:

* **The same adapters and the same services.** Nothing here is MCP-specific at
  the storage, repository or queue level — it composes the identical
  ``FileService`` / ``ConversionService`` / ``TransferService`` the REST API
  uses. That is what makes "MCP cannot do anything the API could not" true by
  construction rather than by discipline.
* **Session lifetime equals request lifetime.** ``open_mcp_scope`` opens one
  ``AsyncSession`` for the duration of a tool call and closes it, exactly like a
  FastAPI request. A cached session would pin a pooled connection open forever
  and leak one user's uncommitted state into the next call.

Everything is exposed through module-level callables that tests can monkeypatch,
because an ASGI sub-application is not reachable from FastAPI's
``dependency_overrides``.
"""

import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from functools import lru_cache

from src.application.services.conversion_service import ConversionService
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.application.services.mcp_toolbox import MCPToolBox
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_conversion_job_repo import (
    SQLConversionJobRepository,
)
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.adapters.security.mcp_oauth_provider import MCPOAuthProvider
from src.infrastructure.adapters.storage.object_copier import MinioObjectCopier
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from src.presentation.api.dependencies.service_dependencies import (
    get_job_queue_port,
    get_minio_download_adapter,
    get_minio_url_storage,
    get_session_cache,
    get_transfer_service,
)


@dataclass
class MCPServiceScope:
    """The services one MCP tool call runs against, plus the caller's tier."""

    file_service: FileService
    conversion_service: ConversionService
    transfer_service: TransferService
    toolbox: MCPToolBox
    tier: SubscriptionTier


@contextlib.asynccontextmanager
async def open_mcp_scope(user_id: int) -> AsyncGenerator[MCPServiceScope]:
    """Build the per-call service bundle for ``user_id`` and dispose of it."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        file_service = FileService(
            file_repository=SQLUserFileRepository(session=session),
            folder_repository=SQLUserFolderRepository(session=session),
            storage=get_minio_url_storage(),
            subscription_repository=SQLSubscriptionRepository(session=session),
        )
        queue_port = get_job_queue_port()
        conversion_service = ConversionService(
            queue_port=queue_port,
            db_repository=SQLConversionJobRepository(session=session),
            queue_dispatcher=PriorityQueueDispatcher(
                queue_port=queue_port,
                router=QueuePriorityRouter(),
            ),
        )
        transfer_service = get_transfer_service(
            storage_port=get_minio_url_storage(),
            cache_port=get_session_cache(),
        )
        # The caller's plan decides the per-file cap and which credits are
        # spent, so it must be read from the database rather than defaulted:
        # defaulting a PRO user to FREE would silently cap their uploads.
        tier = await SQLSubscriptionRepository(session=session).get_tier_for_user(user_id)
        yield MCPServiceScope(
            file_service=file_service,
            conversion_service=conversion_service,
            transfer_service=transfer_service,
            toolbox=MCPToolBox(
                file_service=file_service,
                conversion_service=conversion_service,
                transfer_service=transfer_service,
                copier=MinioObjectCopier(storage=get_minio_download_adapter()),
            ),
            tier=tier,
        )


@lru_cache
def get_mcp_oauth_provider() -> MCPOAuthProvider:
    """The process-wide OAuth provider / token verifier.

    Cached because it is stateless: it derives a short-lived service (and its
    session) per call. Rebuilding it per request would buy nothing.
    """
    settings = get_settings()

    def session_scope():
        return get_session_factory()()

    return MCPOAuthProvider(
        session_scope=session_scope,
        resource_url=settings.mcp_resource_url(),
        consent_url=settings.mcp_consent_url(),
        access_token_ttl_minutes=settings.MCP_ACCESS_TOKEN_TTL_MINUTES,
        refresh_token_ttl_days=settings.MCP_REFRESH_TOKEN_TTL_DAYS,
        authorization_code_ttl_minutes=settings.MCP_AUTHORIZATION_CODE_TTL_MINUTES,
    )
