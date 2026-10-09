"""Request/response schemas for batch conversions and saved workflows."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.presentation.schemas.conversion import ConversionJobResponse

#: How many files one batch request may carry. Enforced at the schema boundary
#: as well as in the service, so an oversized batch is rejected before any work
#: is done rather than part-way through.
MAX_BATCH_FILES = 20


class BatchConversionRequest(BaseModel):
    """Convert several of the caller's library files into one target format."""

    # ``min_length=1`` so an empty batch is a 422 rather than a request that
    # creates nothing and reports success.
    file_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_FILES)
    target_format: str = Field(min_length=1, max_length=20)
    # Present when the batch is a saved workflow's run. The client does not set
    # this on a manual batch: a workflow run goes through its own endpoint, so
    # the id always originates from a workflow the caller owns.
    workflow_id: str | None = Field(default=None, max_length=36)


class BatchItemResult(BaseModel):
    """One file's outcome.

    A failed item carries no job; a successful one carries the job the client
    will then follow on the normal progress stream. The discriminated shape is
    what lets the UI render "3 of 5 started" honestly.
    """

    file_id: str
    file_name: str
    job: ConversionJobResponse | None = None
    error: str | None = None


class BatchConversionResponse(BaseModel):
    batch_id: str
    # ``success`` | ``partial`` | ``failed``, derived from the items.
    status: str
    created_count: int
    failed_count: int
    items: list[BatchItemResult]
    workflow_id: str | None = None


class BatchStatusResponse(BaseModel):
    """A batch as it currently stands, re-read from its jobs.

    This is what makes a batch survive a page reload or a closed browser: the
    client asks for the batch by id and gets its items back, rather than having
    to have remembered the job ids it was told at creation time.
    """

    batch_id: str
    status: str
    total: int
    completed_count: int
    failed_count: int
    active_count: int
    items: list[ConversionJobResponse]
    workflow_id: str | None = None


# ---- Saved workflows ----


class WorkflowDefinitionPayload(BaseModel):
    """An untrusted definition, validated by the domain before it is stored.

    Kept as a permissive object here rather than a fully-typed model on purpose:
    the domain's ``parse_definition`` is the single place that decides what a
    definition may contain, and duplicating those rules as Pydantic constraints
    would give two sources of truth that could disagree.
    """

    source: str | None = None
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=10)


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    definition: WorkflowDefinitionPayload


class UpdateWorkflowRequest(CreateWorkflowRequest):
    """A full replacement — there is no partial edit.

    Renaming and redefining in one call means the stored definition and the name
    a user is looking at can never be from different revisions.
    """


class WorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    description: str
    definition: dict[str, Any]
    run_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowResponse]


class RunWorkflowRequest(BaseModel):
    """Run a workflow against files the caller selects now.

    The files are named per run, never stored in the workflow. A workflow that
    remembered its file ids would rot as those files were deleted or expired,
    and would silently carry the access the user had when they saved it.
    """

    file_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH_FILES)


class WorkflowRunResponse(BaseModel):
    workflow_id: str
    batch_id: str
    status: str
    created_count: int
    failed_count: int
    items: list[BatchItemResult]
    #: Files that were rejected before a job was created (unsupported format, or
    #: no longer accessible). Reported separately from ``items`` so the UI can
    #: explain them without implying a job exists.
    problems: list[dict[str, Any]] = Field(default_factory=list)
