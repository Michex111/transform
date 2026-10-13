"""Request and response schemas for the cloud-workspace API.

Field bounds live here *and* in the domain, and that duplication is intentional:
the schema turns a bad request into a 422 naming the field, while the domain
refuses to construct an invalid entity at all. Neither can be removed without
losing one of those properties.

``mode`` and the lifecycle strings are plain ``str`` fields validated by the
domain rather than ``Literal``/``Enum`` types, so adding a lifecycle state is a
domain change rather than a schema change that an older client fails to parse.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domain.workspaces.entities.execution import EXCERPT_LIMIT_CHARS, MAX_SOURCE_CHARS
from src.domain.workspaces.entities.workspace import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH
from src.domain.workspaces.value_object.project_operation import (
    SUPPORTED_PROJECT_OPERATIONS,
)
from src.domain.workspaces.value_object.resource_profile import RESOURCE_PROFILES
from src.domain.workspaces.value_object.runtime_config import SUPPORTED_PYTHON_VERSIONS

#: Mirrors ``ExecutionService``'s ceiling. Kept as a module constant so the
#: schema and the service cannot disagree about what "too long" means.
MAX_EXECUTION_TIMEOUT_SECONDS = 3_600
MAX_IDEMPOTENCY_KEY_LENGTH = 120
MAX_FILE_WRITE_BYTES = 2 * 1024 * 1024
MAX_FILE_READ_BYTES = 2 * 1024 * 1024
MAX_FILE_PATH_LENGTH = 512


class RuntimeConfigRequest(BaseModel):
    """The environment a workspace should be built from.

    Every field is optional: a client that only wants "a Python workspace" sends
    an empty object and inherits the deployment's defaults. That is the whole
    point of not exposing provider configuration — the supported combination is
    the default.
    """

    model_config = ConfigDict(extra="forbid")

    python_version: str | None = None
    uv_version: str | None = None
    base_image: str | None = None
    base_image_revision: str | None = None
    resource_profile: str | None = None
    config_version: int | None = Field(default=None, ge=1)


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    runtime_config: RuntimeConfigRequest | None = None


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    runtime_config: RuntimeConfigRequest | None = None


class RuntimeConfigResponse(BaseModel):
    python_version: str
    uv_version: str
    base_image: str
    base_image_revision: str
    resource_profile: str
    config_version: int
    image_ref: str


class ResourceProfileResponse(BaseModel):
    name: str
    cpu_cores: float
    memory_mb: int
    pids_limit: int
    disk_mb: int


class RuntimeResponse(BaseModel):
    """The runtime as the client renders it.

    ``provider`` is the provider's short name only — never its handle. The
    provider ref stays server-side: a client that echoed it back would be
    building a contract on an implementation detail that changes whenever the
    runtime is replaced.
    """

    runtime_id: str
    provider: str
    state: str
    resource_profile: str
    image_revision_id: str | None = None
    failure_reason: str | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_activity_at: datetime | None = None


class WorkspaceResponse(BaseModel):
    workspace_id: str
    name: str
    description: str
    state: str
    runtime_config: RuntimeConfigResponse
    execution_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_activity_at: datetime | None = None
    runtime: RuntimeResponse | None = None
    active_executions: int = 0


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceResponse]
    total: int
    page: int
    page_size: int


class RuntimeConfigurationResponse(BaseModel):
    """Everything the Environment tab needs in one round trip."""

    config: RuntimeConfigResponse
    profile: ResourceProfileResponse
    config_fingerprint: str
    #: Set when a compatible saved snapshot exists, so the UI can say "this start
    #: will restore your saved environment" rather than guessing.
    reusable_revision_id: str | None = None
    supported_python_versions: list[str]
    supported_resource_profiles: list[str]
    provider: dict[str, Any]


class ImageRevisionResponse(BaseModel):
    revision_id: str
    status: str
    provider: str
    config_fingerprint: str
    base_image_revision: str
    provider_snapshot_ref: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    ready_at: datetime | None = None


class EnvironmentResponse(BaseModel):
    config: RuntimeConfigResponse
    profile: ResourceProfileResponse
    config_fingerprint: str
    reusable_revision_id: str | None = None
    revisions: list[ImageRevisionResponse]
    #: What survives a stop, stated by the server rather than implied by the UI.
    #: The wording is part of the contract because getting it wrong is how a user
    #: loses work they believed was saved.
    persistence_note: str


class ExecutionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="SCRIPT", max_length=16)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    timeout_seconds: int = Field(default=300, ge=1, le=MAX_EXECUTION_TIMEOUT_SECONDS)
    #: Client-supplied key that makes a retried submit idempotent.
    idempotency_key: str | None = Field(
        default=None, max_length=MAX_IDEMPOTENCY_KEY_LENGTH
    )


class ExecutionArtifactResponse(BaseModel):
    path: str
    size_bytes: int
    content_type: str
    downloadable: bool = False


class ExecutionResponse(BaseModel):
    execution_id: str
    workspace_id: str
    mode: str
    status: str
    source: str
    runtime_id: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    artifacts: list[ExecutionArtifactResponse] = Field(default_factory=list)
    duration_ms: int = 0
    memory_peak_bytes: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    #: True while a worker could still be working on it, so the client knows to
    #: poll rather than assume the result is final.
    settled: bool = False


class ExecutionListResponse(BaseModel):
    executions: list[ExecutionResponse]
    total: int
    page: int
    page_size: int


class ArtifactListResponse(BaseModel):
    artifacts: list[dict[str, Any]]
    #: How many executions were scanned and whether the scan hit its bound, so the
    #: UI can say "recent artifacts" honestly instead of implying "all of them".
    scanned_executions: int
    truncated: bool


class WorkspaceFileEntryResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    size_bytes: int = 0


class WorkspaceFileListResponse(BaseModel):
    path: str
    entries: list[WorkspaceFileEntryResponse]


class WorkspaceFileReadResponse(BaseModel):
    path: str
    size_bytes: int
    content: str
    #: ``utf-8`` when the bytes decoded cleanly, ``base64`` otherwise. A binary
    #: file is returned rather than refused, because "this PNG is not text" is
    #: information the file browser should show, not an error.
    encoding: str
    truncated: bool = False


class WorkspaceFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_FILE_PATH_LENGTH)
    content: str
    encoding: str = Field(default="utf-8", max_length=16)


class WorkspaceFileDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_FILE_PATH_LENGTH)


__all__ = [
    "ArtifactListResponse",
    "EnvironmentResponse",
    "EXCERPT_LIMIT_CHARS",
    "ExecutionArtifactResponse",
    "ExecutionCreateRequest",
    "ExecutionListResponse",
    "ExecutionResponse",
    "ImageRevisionResponse",
    "MAX_FILE_PATH_LENGTH",
    "MAX_FILE_READ_BYTES",
    "MAX_FILE_WRITE_BYTES",
    "RESOURCE_PROFILES",
    "RuntimeConfigRequest",
    "RuntimeConfigResponse",
    "RuntimeConfigurationResponse",
    "RuntimeResponse",
    "SUPPORTED_PROJECT_OPERATIONS",
    "SUPPORTED_PYTHON_VERSIONS",
    "WorkspaceCreateRequest",
    "WorkspaceFileDeleteRequest",
    "WorkspaceFileEntryResponse",
    "WorkspaceFileListResponse",
    "WorkspaceFileReadResponse",
    "WorkspaceFileWriteRequest",
    "WorkspaceListResponse",
    "WorkspaceResponse",
    "WorkspaceUpdateRequest",
]
