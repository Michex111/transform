"""Dependency wiring for the cloud-workspace feature.

Appended to the shared ``service_dependencies`` module so the whole application
keeps one place where a service is assembled from its ports. The split between
``@lru_cache`` and per-request mirrors the rest of that module: anything bound to
the request's ``AsyncSession`` is built per request, and only genuinely
session-independent collaborators are cached.

The runtime provider is cached deliberately even though it is only *used* by the
worker: constructing it is cheap and lazy (it does not connect to a daemon until
the first call), and caching it means the API can answer
``/runtime/configuration`` without building a new adapter per request.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.workspaces.execution_service import ExecutionService
from src.application.services.workspaces.workspace_service import WorkspaceService
from src.infrastructure.adapters.queues.redis_stream_workspace_queue import (
    WorkspaceWorkStream,
)
from src.infrastructure.adapters.repository.sql_execution_repo import (
    SQLExecutionRepository,
)
from src.infrastructure.adapters.repository.sql_workspace_repo import (
    SQLRuntimeImageRevisionRepository,
    SQLRuntimeRepository,
    SQLWorkspaceRepository,
)
from src.infrastructure.adapters.runtime.docker_runtime_provider import (
    DockerRuntimeProvider,
)
from src.infrastructure.adapters.runtime.factory import build_runtime_provider
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session

from src.presentation.api.dependencies.service_dependencies import (
    get_shared_redis_client,
)


@lru_cache
def get_runtime_provider() -> DockerRuntimeProvider:
    return build_runtime_provider(get_settings())


def get_workspace_queue_port() -> WorkspaceWorkStream:
    """The publisher for workspace work.

    Not cached: it is a thin wrapper around the already-shared Redis client, and
    caching a wrapper that holds no state buys nothing.
    """
    return WorkspaceWorkStream(redis_client=get_shared_redis_client())


def get_workspace_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLWorkspaceRepository:
    return SQLWorkspaceRepository(session=db)


def get_runtime_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLRuntimeRepository:
    return SQLRuntimeRepository(session=db)


def get_revision_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLRuntimeImageRevisionRepository:
    return SQLRuntimeImageRevisionRepository(session=db)


def get_execution_repository(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SQLExecutionRepository:
    return SQLExecutionRepository(session=db)


def build_workspace_service(db: AsyncSession) -> WorkspaceService:
    """Assemble the workspace service over one session.

    A plain function rather than only a ``Depends`` factory so the assistant's
    toolbox can build the *same* service over the same request session. It is
    deliberately the same construction: if the AI path assembled its own
    collaborators, the assistant could enforce different rules from the REST
    router, which is exactly the parallel-implementation failure the integration
    was supposed to avoid.
    """
    settings = get_settings()
    return WorkspaceService(
        workspace_repository=SQLWorkspaceRepository(session=db),
        runtime_repository=SQLRuntimeRepository(session=db),
        revision_repository=SQLRuntimeImageRevisionRepository(session=db),
        execution_repository=SQLExecutionRepository(session=db),
        runtime_provider=get_runtime_provider(),
        queue=get_workspace_queue_port(),
        max_workspaces=settings.WORKSPACE_MAX_PER_USER,
        snapshot_retention=settings.WORKSPACE_SNAPSHOT_RETENTION,
        disk_quota_bytes=settings.WORKSPACE_DISK_QUOTA_MB * 1024 * 1024,
    )


def build_execution_service(db: AsyncSession) -> ExecutionService:
    """Assemble the execution service over one session (see above).

    The egress policy is consulted here so an isolated deployment refuses a
    package installation *before* it queues anything, rather than letting the
    resolver time out inside a runtime that has no network.
    """
    settings = get_settings()
    provider = get_runtime_provider()
    return ExecutionService(
        execution_repository=SQLExecutionRepository(session=db),
        workspace_repository=SQLWorkspaceRepository(session=db),
        queue=get_workspace_queue_port(),
        max_active_executions=settings.WORKSPACE_MAX_ACTIVE_EXECUTIONS,
        capacity=build_workspace_service(db),
        allows_package_install=provider.allows_package_install,
    )


def get_workspace_service(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    provider: Annotated[DockerRuntimeProvider, Depends(get_runtime_provider)],
    queue: Annotated[WorkspaceWorkStream, Depends(get_workspace_queue_port)],
) -> WorkspaceService:
    # ``provider`` and ``queue`` are declared so FastAPI resolves them (and so a
    # test can override either); the builder reads them back through their own
    # factories, which are cached or cheap.
    del provider, queue
    return build_workspace_service(db)


def get_execution_service(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    queue: Annotated[WorkspaceWorkStream, Depends(get_workspace_queue_port)],
) -> ExecutionService:
    del queue
    return build_execution_service(db)
