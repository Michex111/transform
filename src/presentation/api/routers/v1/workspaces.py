"""Cloud-workspace API: workspaces, runtimes, executions, environment, files.

Every endpoint is owner-scoped and every domain/service failure is mapped to a
status code *here* rather than leaking an exception into a 500. The mapping is
centralised in ``_handle_errors`` so a new endpoint cannot accidentally answer a
different way for the same condition.

Nothing in this module executes user code, and nothing here talks to a provider
directly: the runtime provider is reached only through ``WorkspaceService``, so a
provider detail cannot leak into a response schema by accident.
"""

import base64
import binascii
from typing import Annotated, Any

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.application.exceptions.runtime_provider_exceptions import (
    ProviderExecutionError,
    ProviderResourceError,
    RuntimeNotFoundError as ProviderRuntimeNotFoundError,
    RuntimeProviderError,
    RuntimeProviderUnavailableError,
)
from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    RuntimeBusyError,
    RuntimeNotFoundError,
    RuntimeNotRunningError,
    SnapshotFailedError,
    WorkspaceLimitReachedError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
    WorkspaceQuotaExceededError,
)
from src.application.services.execution_service import (
    ExecutionArtifactRef,
    ExecutionNotFoundError,
    ExecutionService,
    InvalidExecutionRequestError,
    TooManyActiveExecutionsError,
)
from src.application.services.workspace_service import WorkspaceDetail, WorkspaceService
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.entities.workspace import Workspace
from src.domain.workspaces.entities.workspace_runtime import WorkspaceRuntime
from src.domain.workspaces.exceptions import InvalidRuntimeConfigError, InvalidWorkspaceError
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import (
    TERMINAL_EXECUTION_STATUSES,
)
from src.domain.workspaces.value_object.resource_profile import RESOURCE_PROFILES
from src.domain.workspaces.value_object.runtime_config import (
    RuntimeConfig,
    SUPPORTED_PYTHON_VERSIONS,
)
from src.presentation.api.dependencies.auth_dependencies import CurrentUser, RequestOrigin
from src.presentation.api.dependencies.workspace_dependencies import (
    get_execution_service,
    get_workspace_service,
)
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioUrlStorageAdapter
from src.presentation.api.dependencies.service_dependencies import get_minio_url_storage
from src.presentation.schemas.workspace import (
    MAX_FILE_READ_BYTES,
    MAX_FILE_WRITE_BYTES,
    ArtifactListResponse,
    EnvironmentResponse,
    ExecutionCreateRequest,
    ExecutionListResponse,
    ExecutionResponse,
    ImageRevisionResponse,
    RuntimeConfigRequest,
    RuntimeConfigResponse,
    RuntimeConfigurationResponse,
    RuntimeResponse,
    WorkspaceCreateRequest,
    WorkspaceFileDeleteRequest,
    WorkspaceFileEntryResponse,
    WorkspaceFileListResponse,
    WorkspaceFileReadResponse,
    WorkspaceFileWriteRequest,
    WorkspaceListResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["workspaces"])

WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]

#: The exact wording the UI shows before a stop. Server-provided on purpose: what
#: survives a stop is a property of the provider, and hard-coding it in the SPA
#: would make it wrong the day the provider changes.
_PERSISTENCE_NOTE = (
    "Your files, pyproject.toml and uv.lock live on the workspace's durable volume "
    "and survive stopping, restarting and replacing the runtime. Installed packages "
    "survive an ordinary stop and restart of the same runtime; if the runtime is "
    "replaced they are restored from a saved environment snapshot, or rebuilt with "
    "uv sync from your lockfile. Running processes and in-memory Python variables "
    "(including anything held in a variable in a REPL) are never preserved."
)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _handle_errors(exc: Exception) -> HTTPException:
    """Map a domain/service/provider failure to the right HTTP response."""
    if isinstance(exc, WorkspaceNotFoundError):
        return _http(status.HTTP_404_NOT_FOUND, "Workspace not found.")
    if isinstance(exc, ExecutionNotFoundError):
        return _http(status.HTTP_404_NOT_FOUND, "Execution not found.")
    if isinstance(exc, RuntimeNotFoundError):
        return _http(
            status.HTTP_409_CONFLICT,
            "This workspace has no runtime yet. Start it first.",
        )
    if isinstance(exc, RuntimeNotRunningError):
        return _http(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, RuntimeBusyError):
        return _http(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, WorkspaceNotUsableError):
        return _http(
            status.HTTP_409_CONFLICT,
            "This workspace is archived, so it does not accept new work.",
        )
    if isinstance(exc, WorkspaceLimitReachedError):
        return _http(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, TooManyActiveExecutionsError):
        return _http(status.HTTP_429_TOO_MANY_REQUESTS, str(exc))
    if isinstance(exc, WorkspaceQuotaExceededError):
        # 507 rather than 413: the request is fine, the destination is full, and
        # the numbers travel with it so the client can tell the user how much to
        # delete. (413 is already used for "this single file is too large".)
        return _http(status.HTTP_507_INSUFFICIENT_STORAGE, str(exc))
    if isinstance(exc, PackageInstallNotPermittedError):
        # 403, not 422: the request is well-formed and the user may be entitled to
        # it — the deployment's egress policy is what forbids it.
        return _http(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, (InvalidRuntimeConfigError,)):
        return _http(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if isinstance(exc, InvalidWorkspaceError):
        # A name that is blank *after* whitespace normalisation, or one that is
        # too long once collapsed, is refused by the domain rather than the
        # schema — so it has to be mapped here or it becomes a 500.
        return _http(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if isinstance(exc, InvalidExecutionRequestError):
        # Covers an unknown execution mode and a rejected project operation, both
        # of which are the caller's mistake rather than the server's.
        return _http(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if isinstance(exc, SnapshotFailedError):
        return _http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The environment snapshot could not be queued. Please try again.",
        )
    if isinstance(exc, (RuntimeProviderUnavailableError, ProviderExecutionError)):
        # Infrastructure, not the user's code. The provider's own message names
        # hosts and sockets, so only a generic sentence is returned.
        return _http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The execution host is currently unavailable. Please try again shortly.",
        )
    if isinstance(exc, ProviderRuntimeNotFoundError):
        return _http(
            status.HTTP_409_CONFLICT,
            "The runtime no longer exists on the execution host. Start it again.",
        )
    if isinstance(exc, (ProviderResourceError, RuntimeProviderError)):
        return _http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The execution host could not complete that operation.",
        )
    raise exc


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


def _config_response(config: RuntimeConfig) -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        python_version=config.python_version,
        uv_version=config.uv_version,
        base_image=config.base_image,
        base_image_revision=config.base_image_revision,
        resource_profile=config.resource_profile,
        config_version=config.config_version,
        image_ref=config.image_ref,
    )


def _runtime_response(runtime: WorkspaceRuntime | None) -> RuntimeResponse | None:
    if runtime is None:
        return None
    return RuntimeResponse(
        runtime_id=runtime.runtime_id,
        provider=runtime.provider,
        state=str(runtime.state),
        resource_profile=runtime.resource_profile,
        image_revision_id=runtime.image_revision_id,
        failure_reason=runtime.failure_reason,
        started_at=runtime.started_at,
        stopped_at=runtime.stopped_at,
        last_activity_at=runtime.last_activity_at,
    )


def _workspace_response(
    workspace: Workspace,
    *,
    runtime: WorkspaceRuntime | None = None,
    active_executions: int = 0,
) -> WorkspaceResponse:
    return WorkspaceResponse(
        workspace_id=workspace.workspace_id,
        name=workspace.name,
        description=workspace.description,
        state=str(workspace.state),
        runtime_config=_config_response(workspace.runtime_config),
        execution_count=workspace.execution_count,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        last_activity_at=workspace.last_activity_at,
        runtime=_runtime_response(runtime),
        active_executions=active_executions,
    )


def _execution_response(execution: Execution) -> ExecutionResponse:
    return ExecutionResponse(
        execution_id=execution.execution_id,
        workspace_id=execution.workspace_id,
        mode=str(execution.mode),
        status=str(execution.status),
        source=execution.source,
        runtime_id=execution.runtime_id,
        exit_code=execution.exit_code,
        stdout=execution.stdout_excerpt,
        stderr=execution.stderr_excerpt,
        stdout_truncated=execution.stdout_truncated,
        stderr_truncated=execution.stderr_truncated,
        error=execution.error,
        artifacts=[
            {
                "path": artifact.path,
                "size_bytes": artifact.size_bytes,
                "content_type": artifact.content_type,
                "downloadable": artifact.object_key is not None,
            }
            for artifact in execution.artifacts
        ],
        duration_ms=execution.duration_ms,
        memory_peak_bytes=execution.memory_peak_bytes,
        created_at=execution.created_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        settled=execution.status in TERMINAL_EXECUTION_STATUSES,
    )


def _revision_response(revision: RuntimeImageRevision) -> ImageRevisionResponse:
    return ImageRevisionResponse(
        revision_id=revision.revision_id,
        status=str(revision.status),
        provider=revision.provider,
        config_fingerprint=revision.config_fingerprint,
        base_image_revision=revision.base_image_revision,
        provider_snapshot_ref=revision.provider_snapshot_ref,
        error=revision.error,
        created_at=revision.created_at,
        ready_at=revision.ready_at,
    )


def _config_from_request(payload: RuntimeConfigRequest | None) -> RuntimeConfig | None:
    """Fill an omitted field from the deployment default.

    Only the fields the client actually sent are overridden, so "send nothing"
    and "send an empty object" mean the same thing: the supported default.
    """
    if payload is None:
        return None
    defaults = RuntimeConfig()
    return RuntimeConfig(
        python_version=payload.python_version or defaults.python_version,
        uv_version=payload.uv_version or defaults.uv_version,
        base_image=payload.base_image or defaults.base_image,
        base_image_revision=payload.base_image_revision or defaults.base_image_revision,
        resource_profile=payload.resource_profile or defaults.resource_profile,
        config_version=payload.config_version or defaults.config_version,
    )


def _parse_mode(raw: str) -> ExecutionMode:
    try:
        return ExecutionMode(str(raw).strip().upper())
    except ValueError as exc:
        supported = ", ".join(member.value for member in ExecutionMode)
        raise InvalidExecutionRequestError(
            f"Unsupported execution mode {raw!r}; supported: {supported}."
        ) from exc


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
async def create_workspace(
    payload: WorkspaceCreateRequest,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    try:
        workspace = await service.create_workspace(
            user_id=current_user.id,
            name=payload.name,
            description=payload.description,
            runtime_config=_config_from_request(payload.runtime_config),
        )
    except Exception as exc:  # noqa: BLE001 - mapped centrally
        raise _handle_errors(exc) from exc
    return _workspace_response(workspace)


@router.get(
    "/workspaces",
    response_model=WorkspaceListResponse,
    summary="List your workspaces",
)
async def list_workspaces(
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> WorkspaceListResponse:
    try:
        workspaces, total = await service.list_workspaces(
            current_user.id, limit=page_size, offset=(page - 1) * page_size
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc

    # One runtime lookup per row is acceptable at this page size and keeps the
    # list honest: the runtime state shown is the state in the database, not a
    # cached guess.
    responses: list[WorkspaceResponse] = []
    for workspace in workspaces:
        runtime = await service.runtime_repository.get_runtime_for_workspace(
            workspace.workspace_id
        )
        responses.append(_workspace_response(workspace, runtime=runtime))
    return WorkspaceListResponse(
        workspaces=responses, total=total, page=page, page_size=page_size
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Get one workspace",
)
async def get_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    try:
        detail: WorkspaceDetail = await service.get_detail(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _workspace_response(
        detail.workspace,
        runtime=detail.runtime,
        active_executions=detail.active_executions,
    )


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Rename, re-describe or reconfigure a workspace",
)
async def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdateRequest,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    try:
        workspace = await service.update_workspace(
            workspace_id=workspace_id,
            user_id=current_user.id,
            name=payload.name,
            description=payload.description,
            runtime_config=_config_from_request(payload.runtime_config),
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _workspace_response(workspace)


@router.delete(
    "/workspaces/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workspace and all of its resources",
)
async def delete_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> Response:
    try:
        await service.delete_workspace(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceResponse,
    summary="Archive a workspace (keeps every file, refuses new work)",
)
async def archive_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    try:
        workspace = await service.archive_workspace(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _workspace_response(workspace)


@router.post(
    "/workspaces/{workspace_id}/reactivate",
    response_model=WorkspaceResponse,
    summary="Reactivate an archived workspace",
)
async def reactivate_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    try:
        workspace = await service.reactivate_workspace(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _workspace_response(workspace)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/runtime",
    response_model=RuntimeResponse | None,
    summary="The workspace's runtime, if it has ever been started",
)
async def get_runtime(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> RuntimeResponse | None:
    try:
        runtime = await service.get_runtime(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _runtime_response(runtime)


@router.post(
    "/workspaces/{workspace_id}/runtime/start",
    response_model=RuntimeResponse,
    summary="Start the runtime (idempotent)",
)
async def start_runtime(
    workspace_id: str,
    response: Response,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> RuntimeResponse:
    try:
        started = await service.start_runtime(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    # 200 when the runtime was already up (nothing to wait for), 202 when this
    # call actually asked the provider to allocate or resume one.
    response.status_code = (
        status.HTTP_202_ACCEPTED if started.changed else status.HTTP_200_OK
    )
    return _runtime_response(started.runtime)  # type: ignore[return-value]


@router.post(
    "/workspaces/{workspace_id}/runtime/stop",
    response_model=RuntimeResponse,
    summary="Stop the runtime, preserving everything durable",
)
async def stop_runtime(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
    force: Annotated[bool, Query()] = False,
) -> RuntimeResponse:
    try:
        runtime = await service.stop_runtime(workspace_id, current_user.id, force=force)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _runtime_response(runtime)  # type: ignore[return-value]


@router.post(
    "/workspaces/{workspace_id}/runtime/restart",
    response_model=RuntimeResponse,
    summary="Stop and start the runtime",
)
async def restart_runtime(
    workspace_id: str,
    response: Response,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
    force: Annotated[bool, Query()] = False,
) -> RuntimeResponse:
    try:
        started = await service.restart_runtime(workspace_id, current_user.id, force=force)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    response.status_code = (
        status.HTTP_202_ACCEPTED if started.changed else status.HTTP_200_OK
    )
    return _runtime_response(started.runtime)  # type: ignore[return-value]


@router.get(
    "/workspaces/{workspace_id}/runtime/configuration",
    response_model=RuntimeConfigurationResponse,
    summary="The runtime configuration and the provider's capabilities",
)
async def runtime_configuration(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> RuntimeConfigurationResponse:
    try:
        described = await service.runtime_configuration(workspace_id, current_user.id)
        provider = await service.provider_description()
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    config: RuntimeConfig = described["config"]
    profile = described["profile"]
    return RuntimeConfigurationResponse(
        config=_config_response(config),
        profile={
            "name": profile.name,
            "cpu_cores": profile.cpu_cores,
            "memory_mb": profile.memory_mb,
            "pids_limit": profile.pids_limit,
            "disk_mb": profile.disk_mb,
        },
        config_fingerprint=described["config_fingerprint"],
        reusable_revision_id=described["reusable_revision_id"],
        supported_python_versions=sorted(SUPPORTED_PYTHON_VERSIONS),
        supported_resource_profiles=sorted(RESOURCE_PROFILES),
        provider=provider,
    )


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/environment",
    response_model=EnvironmentResponse,
    summary="Python/uv versions, dependency configuration and snapshot status",
)
async def get_environment(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> EnvironmentResponse:
    try:
        described = await service.runtime_configuration(workspace_id, current_user.id)
        revisions = await service.list_revisions(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    config: RuntimeConfig = described["config"]
    profile = described["profile"]
    return EnvironmentResponse(
        config=_config_response(config),
        profile={
            "name": profile.name,
            "cpu_cores": profile.cpu_cores,
            "memory_mb": profile.memory_mb,
            "pids_limit": profile.pids_limit,
            "disk_mb": profile.disk_mb,
        },
        config_fingerprint=described["config_fingerprint"],
        reusable_revision_id=described["reusable_revision_id"],
        revisions=[_revision_response(revision) for revision in revisions],
        persistence_note=_PERSISTENCE_NOTE,
    )


@router.post(
    "/workspaces/{workspace_id}/environment/save",
    response_model=ImageRevisionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Save the current environment as a reusable snapshot",
)
async def save_environment(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> ImageRevisionResponse:
    try:
        revision = await service.save_environment(workspace_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _revision_response(revision)


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/executions",
    response_model=ExecutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit Python code, a command or a project operation",
)
async def submit_execution(
    workspace_id: str,
    payload: ExecutionCreateRequest,
    current_user: CurrentUser,
    origin: RequestOrigin,
    service: ExecutionServiceDep,
) -> ExecutionResponse:
    try:
        # Parsed inside the try so an unknown mode becomes the same 422 as any
        # other rejected request rather than an unhandled exception.
        mode = _parse_mode(payload.mode)
        execution = await service.submit(
            workspace_id=workspace_id,
            user_id=current_user.id,
            mode=mode,
            source=payload.source,
            timeout_seconds=payload.timeout_seconds,
            idempotency_key=payload.idempotency_key,
            origin=JobOrigin(str(origin)) if origin else JobOrigin.WEB,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _execution_response(execution)


@router.get(
    "/workspaces/{workspace_id}/executions",
    response_model=ExecutionListResponse,
    summary="Execution history for a workspace",
)
async def list_executions(
    workspace_id: str,
    current_user: CurrentUser,
    service: ExecutionServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ExecutionListResponse:
    try:
        executions, total = await service.list_for_workspace(
            workspace_id=workspace_id,
            user_id=current_user.id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return ExecutionListResponse(
        executions=[_execution_response(item) for item in executions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionResponse,
    summary="One execution, with its captured output",
)
async def get_execution(
    execution_id: str,
    current_user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionResponse:
    try:
        execution = await service.get(execution_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _execution_response(execution)


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=ExecutionResponse,
    summary="Cancel an execution (idempotent)",
)
async def cancel_execution(
    execution_id: str,
    current_user: CurrentUser,
    service: ExecutionServiceDep,
) -> ExecutionResponse:
    try:
        execution = await service.cancel(execution_id, current_user.id)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return _execution_response(execution)


@router.get(
    "/workspaces/{workspace_id}/artifacts",
    response_model=ArtifactListResponse,
    summary="Artifacts produced by recent executions",
)
async def list_artifacts(
    workspace_id: str,
    current_user: CurrentUser,
    service: ExecutionServiceDep,
) -> ArtifactListResponse:
    try:
        refs: list[ExecutionArtifactRef] = await service.list_artifacts(
            workspace_id=workspace_id, user_id=current_user.id
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return ArtifactListResponse(
        artifacts=[
            {
                "execution_id": ref.execution_id,
                "path": ref.artifact.path,
                "size_bytes": ref.artifact.size_bytes,
                "content_type": ref.artifact.content_type,
                # An artifact that was never copied out of the runtime has no
                # object key, and saying it is downloadable would be a lie.
                "downloadable": ref.artifact.object_key is not None,
            }
            for ref in refs
        ],
        scanned_executions=min(len(refs), 50),
        truncated=len(refs) >= 200,
    )


@router.get(
    "/executions/{execution_id}/artifacts/download",
    summary="Mint a download URL for an artifact produced by an execution",
)
async def download_artifact(
    execution_id: str,
    current_user: CurrentUser,
    service: ExecutionServiceDep,
    storage: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    path: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, Any]:
    """Resolve an owned artifact to a short-lived object-storage URL.

    The URL is minted only after ownership is checked against the execution, so
    knowing an artifact's path is not a way to read it. A presigned URL rather
    than a streamed proxy keeps large artifacts off the API process — the same
    choice the rest of the product makes for stored files.

    404 for an artifact that was recorded but never copied (its run produced it
    while object storage was unreachable): there are no bytes to serve, and
    saying so is better than a URL that 404s at the bucket.
    """
    try:
        artifact = await service.get_artifact(execution_id, current_user.id, path)
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    if artifact is None:
        raise _http(
            status.HTTP_404_NOT_FOUND,
            "That artifact is not available to download.",
        )
    assert artifact.object_key is not None  # guaranteed by get_artifact
    try:
        # The adapter's URL minting is synchronous (it signs locally), so it runs
        # off the loop only to keep a slow clock/credential read from blocking.
        url = await asyncio.to_thread(storage.generate_get_url, artifact.object_key)
    except Exception as exc:  # noqa: BLE001 - an expired credential is a 503
        raise _http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The stored artifact could not be reached. Please try again shortly.",
        ) from exc
    return {
        "url": url,
        "path": artifact.path,
        "size_bytes": artifact.size_bytes,
        "content_type": artifact.content_type,
    }


# ---------------------------------------------------------------------------
# Workspace files
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/files",
    response_model=WorkspaceFileListResponse,
    summary="List a directory inside the workspace",
)
async def list_workspace_files(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
    path: Annotated[str, Query(max_length=512)] = ".",
) -> WorkspaceFileListResponse:
    try:
        entries = await service.list_workspace_files(
            workspace_id, current_user.id, path=path
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return WorkspaceFileListResponse(
        path=path,
        entries=[
            WorkspaceFileEntryResponse(
                path=entry.path,
                name=entry.name,
                is_dir=entry.is_dir,
                size_bytes=entry.size_bytes,
            )
            for entry in entries
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/files/content",
    response_model=WorkspaceFileReadResponse,
    summary="Read a file from the workspace",
)
async def read_workspace_file(
    workspace_id: str,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
    path: Annotated[str, Query(min_length=1, max_length=512)],
) -> WorkspaceFileReadResponse:
    try:
        data = await service.read_workspace_file(
            workspace_id, current_user.id, path=path
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc

    truncated = len(data) > MAX_FILE_READ_BYTES
    payload = data[:MAX_FILE_READ_BYTES]
    try:
        content = payload.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        # A binary file is reported as base64 rather than refused: "this is not
        # text" is useful information for a file browser, not an error.
        content = base64.b64encode(payload).decode("ascii")
        encoding = "base64"
    return WorkspaceFileReadResponse(
        path=path, size_bytes=len(data), content=content, encoding=encoding,
        truncated=truncated,
    )


@router.put(
    "/workspaces/{workspace_id}/files/content",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Write a file into the workspace",
)
async def write_workspace_file(
    workspace_id: str,
    payload: WorkspaceFileWriteRequest,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> Response:
    try:
        if payload.encoding == "base64":
            try:
                data = base64.b64decode(payload.content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid base64 content.") from exc
        else:
            data = payload.content.encode("utf-8")
        if len(data) > MAX_FILE_WRITE_BYTES:
            raise _http(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"A workspace file write is limited to {MAX_FILE_WRITE_BYTES} bytes.",
            )
        await service.write_workspace_file(
            workspace_id, current_user.id, path=payload.path, data=data
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/files/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file or directory from the workspace",
)
async def delete_workspace_file(
    workspace_id: str,
    payload: WorkspaceFileDeleteRequest,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
) -> Response:
    try:
        await service.delete_workspace_file(
            workspace_id, current_user.id, path=payload.path
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle_errors(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__: list[Any] = ["router"]
