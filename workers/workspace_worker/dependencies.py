"""Collaborators for the workspace worker, built from settings.

Mirrors ``workers/converter_workers/dependencies.py``: the worker holds a session
*factory* rather than a session, because it outlives any single unit of work and
a long-lived session would pin a pooled database connection open across idle
periods.
"""

from dataclasses import dataclass
from typing import Any

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
from src.infrastructure.adapters.storage.minio_storage_factory import get_storage
from src.infrastructure.config.settings import Settings, get_settings
from src.infrastructure.database.session import get_session_factory
from src.infrastructure.redis.client import create_redis_client


@dataclass
class WorkspaceWorkerContext:
    """Everything one worker process needs, resolved once at startup."""

    settings: Settings
    session_factory: Any
    provider: DockerRuntimeProvider
    queue: WorkspaceWorkStream
    redis_client: Any
    consumer_group: str
    execution_timeout_seconds: int
    max_output_bytes: int
    snapshot_retention: int
    storage: Any = None
    """Object storage, used to copy execution artifacts out of the runtime.

    Held here rather than fetched per execution because the adapter owns a
    connection pool; building one per job would throw away every keep-alive.
    ``None`` disables copying, which is honest in a test that is not about
    artifacts — the execution then records paths without object keys, exactly as
    it did before this existed.
    """
    max_artifacts_per_execution: int = 20
    max_artifact_bytes: int = 25 * 1024 * 1024
    artifact_directory: str = "outputs"

    def build_service(self, session: Any) -> WorkspaceService:
        """The same service the API uses, over this unit of work's session.

        Deliberately the *same* class rather than a worker-specific shortcut: the
        runtime-start rules (idempotency, snapshot reuse, configuration
        fingerprints) exist once, so a queued execution cannot start a runtime by
        a path the API would have refused.
        """
        return WorkspaceService(
            workspace_repository=SQLWorkspaceRepository(session=session),
            runtime_repository=SQLRuntimeRepository(session=session),
            revision_repository=SQLRuntimeImageRevisionRepository(session=session),
            execution_repository=SQLExecutionRepository(session=session),
            runtime_provider=self.provider,
            queue=self.queue,
            snapshot_retention=self.snapshot_retention,
        )


def build_context(settings: Settings | None = None) -> WorkspaceWorkerContext:
    settings = settings or get_settings()
    redis_client = create_redis_client(settings.REDIS_URL.get_secret_value())
    return WorkspaceWorkerContext(
        settings=settings,
        session_factory=get_session_factory(),
        provider=build_runtime_provider(settings),
        queue=WorkspaceWorkStream(redis_client=redis_client),
        redis_client=redis_client,
        consumer_group=settings.WORKER_CONSUMER_GROUP,
        execution_timeout_seconds=settings.WORKSPACE_EXECUTION_TIMEOUT_SECONDS,
        max_output_bytes=settings.WORKSPACE_MAX_OUTPUT_BYTES,
        snapshot_retention=settings.WORKSPACE_SNAPSHOT_RETENTION,
        storage=get_storage(),
        max_artifacts_per_execution=settings.WORKSPACE_MAX_ARTIFACTS,
        max_artifact_bytes=settings.WORKSPACE_MAX_ARTIFACT_BYTES,
        artifact_directory=settings.WORKSPACE_ARTIFACT_DIRECTORY,
    )
