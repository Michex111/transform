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
from src.application.services.credit_service import CreditService
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.application.services.mcp_toolbox import MCPToolBox
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_conversion_job_repo import (
    SQLConversionJobRepository,
)
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository
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
    """The services one MCP tool call runs against, plus the caller's binding."""

    file_service: FileService
    conversion_service: ConversionService
    transfer_service: TransferService
    credit_service: CreditService
    toolbox: MCPToolBox
    tier: SubscriptionTier
    #: The grant's folder confinement and history scope, threaded from the
    #: ``AgentGrant`` this token was minted for. Carried here (not read again in
    #: the toolbox) so one call has one consistent view of the binding.
    folder_access: FolderAccess = FolderAccess.ALL
    folder_id: str | None = None
    history_scope: HistoryScope = HistoryScope.AGENT


@contextlib.asynccontextmanager
async def open_mcp_scope(
    user_id: int, grant_id: str | None = None
) -> AsyncGenerator[MCPServiceScope]:
    """Build the per-call service bundle for ``user_id`` and dispose of it.

    ``grant_id`` names the ``AgentGrant`` the access token was issued from. It is
    loaded here — on the same session the services use — so the folder binding
    and history scope the tools enforce come from the stored grant rather than
    from anything the model can influence. If the grant cannot be found, or does
    not belong to ``user_id``, the fail-closed defaults are kept
    (``ALL``/``None``/``AGENT``); note that a ``FOLDER`` binding with no folder is
    then denied outright by the toolbox.
    """
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
        subscription_repository = SQLSubscriptionRepository(session=session)
        transfer_service = get_transfer_service(
            storage_port=get_minio_url_storage(),
            cache_port=get_session_cache(),
        )
        credit_service = CreditService(
            credit_repository=SQLCreditRepository(session=session),
            subscription_repository=subscription_repository,
        )
        # The caller's plan decides the per-file cap and which credits are
        # spent, so it must be read from the database rather than defaulted:
        # defaulting a PRO user to FREE would silently cap their uploads.
        tier = await subscription_repository.get_tier_for_user(user_id)

        folder_access = FolderAccess.ALL
        folder_id: str | None = None
        history_scope = HistoryScope.AGENT
        if grant_id:
            grant = await SQLMCPRepository(session=session).get_grant(grant_id)
            # ``user_id`` is re-checked here even though the token's subject was
            # already validated: a token must never inherit a binding from a
            # grant belonging to another account, however it was presented.
            if grant is not None and grant.user_id == user_id:
                folder_access = grant.folder_access
                folder_id = grant.folder_id
                history_scope = grant.history_scope

        object_store = get_minio_download_adapter()
        yield MCPServiceScope(
            file_service=file_service,
            conversion_service=conversion_service,
            transfer_service=transfer_service,
            credit_service=credit_service,
            toolbox=MCPToolBox(
                file_service=file_service,
                conversion_service=conversion_service,
                transfer_service=transfer_service,
                copier=MinioObjectCopier(storage=get_minio_download_adapter()),
                credits=credit_service,
                object_store=object_store,
            ),
            tier=tier,
            folder_access=folder_access,
            folder_id=folder_id,
            history_scope=history_scope,
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
