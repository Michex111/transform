"""Saved workflow API: manage definitions and run them.

A run is the interesting endpoint. It does three things in a fixed order, and
the order is the security property:

1. **Load the workflow** by id, scoped to the caller. A workflow id from another
   account is a 404, never a usable template.
2. **Re-resolve and re-authorize the selected files.** The workflow stores no
   file ids, so there is no stale grant to honour: every file is looked up
   against ``current_user.id`` at run time, through the same query the batch
   endpoint uses.
3. **Validate the whole selection before starting anything.** Unsupported files
   are reported up front rather than surfacing one failure at a time.

Only then does it delegate to ``ConversionService.create_batch`` — the same path
a manual batch takes. A workflow therefore gets progress, retry, download,
credits and history for free, and has no execution semantics of its own that
could drift from the rest of the product.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.services.conversion_service import BatchItemRequest, ConversionService
from src.application.services.file_service import FileService
from src.application.services.workflow_service import (
    WorkflowNotFoundError,
    WorkflowRunFile,
    WorkflowService,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.workflows.entities.saved_workflow import InvalidWorkflowError
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.storage.sanitize import extension_from_filename
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.auth_dependencies import CurrentUser, RequestOrigin
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_service,
    get_file_service,
    get_subscription_repository,
    get_workflow_service,
)
from src.presentation.schemas.workflow import (
    CreateWorkflowRequest,
    RunWorkflowRequest,
    UpdateWorkflowRequest,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowRunResponse,
)
from src.presentation.api.routers.v1.conversions import (
    _to_batch_response,
    _to_response,
    _tier_for,
)
from src.presentation.schemas.workflow import BatchItemResult

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def _to_workflow_response(workflow, run_count: int = 0) -> WorkflowResponse:
    return WorkflowResponse(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        definition=workflow.definition.to_dict(),
        run_count=run_count,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _not_found() -> HTTPException:
    # Missing and not-owned are the same response on purpose: distinguishing
    # them would confirm another account's workflow to anyone guessing an id.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    current_user: CurrentUser,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
    conversions: Annotated[ConversionService, Depends(get_conversion_service)],
) -> WorkflowListResponse:
    """The caller's saved workflows, newest first, with their run counts.

    The counts come from one grouped query rather than one per workflow: they
    are only ever a badge, and a per-row count would be N+1 round trips for a
    number the list does not need to be exact about.
    """
    rows = await workflows.list_workflows(current_user.id)
    counts = await conversions.count_workflow_runs(
        [row.workflow_id for row in rows], current_user.id
    )
    return WorkflowListResponse(
        workflows=[_to_workflow_response(row, counts.get(row.workflow_id, 0)) for row in rows]
    )


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: CreateWorkflowRequest,
    current_user: CurrentUser,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
) -> WorkflowResponse:
    """Save a new workflow.

    The definition is validated by the domain before it is stored, so a
    malformed one is rejected here rather than failing a run later — long after
    the user could connect the failure to what they saved.
    """
    try:
        workflow = await workflows.create(
            user_id=current_user.id,
            name=payload.name,
            description=payload.description,
            definition=payload.definition.model_dump(exclude_none=True),
        )
    except InvalidWorkflowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _to_workflow_response(workflow)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    current_user: CurrentUser,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
    conversions: Annotated[ConversionService, Depends(get_conversion_service)],
) -> WorkflowResponse:
    try:
        workflow = await workflows.get(workflow_id, current_user.id)
    except WorkflowNotFoundError as exc:
        raise _not_found() from exc
    counts = await conversions.count_workflow_runs([workflow_id], current_user.id)
    return _to_workflow_response(workflow, counts.get(workflow_id, 0))


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    payload: UpdateWorkflowRequest,
    current_user: CurrentUser,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
) -> WorkflowResponse:
    """Replace a workflow's name, description and definition in one call.

    A full replacement rather than a partial edit, so the name the user sees and
    the definition that will run can never come from different revisions.
    """
    try:
        workflow = await workflows.update(
            workflow_id=workflow_id,
            user_id=current_user.id,
            name=payload.name,
            description=payload.description,
            definition=payload.definition.model_dump(exclude_none=True),
        )
    except WorkflowNotFoundError as exc:
        raise _not_found() from exc
    except InvalidWorkflowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _to_workflow_response(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    current_user: CurrentUser,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
) -> Response:
    """Delete a workflow.

    The conversions it produced are untouched. They are the user's own record of
    work that really happened, and deleting the shortcut that created them must
    not erase them.
    """
    try:
        await workflows.delete(workflow_id, current_user.id)
    except WorkflowNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{workflow_id}/runs",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_workflow(
    workflow_id: str,
    payload: RunWorkflowRequest,
    current_user: CurrentUser,
    origin: RequestOrigin,
    workflows: Annotated[WorkflowService, Depends(get_workflow_service)],
    file_service: Annotated[FileService, Depends(get_file_service)],
    conversions: Annotated[ConversionService, Depends(get_conversion_service)],
    subscriptions: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> WorkflowRunResponse:
    """Run a workflow against files the caller selects now.

    Returns ``202`` with a per-item outcome, exactly like a manual batch: the
    run starts jobs and hands back their ids, and the client follows each on the
    normal progress stream. Nothing here waits for a conversion to finish.
    """
    try:
        workflow = await workflows.get(workflow_id, current_user.id)
    except WorkflowNotFoundError as exc:
        raise _not_found() from exc

    workflows.enforce_batch_limit(len(payload.file_ids))

    # One query for the whole selection, scoped to the caller. This is the
    # re-authorization step: the workflow holds no file references, so nothing
    # it was saved with can outlive the user's current access.
    rows = await file_service.get_owned_files(current_user.id, payload.file_ids)
    found_ids = {row.id for row in rows}

    problems: list[BatchItemResult] = [
        BatchItemResult(
            file_id=file_id,
            file_name="",
            error="That file is no longer available.",
        )
        for file_id in payload.file_ids
        if file_id not in found_ids
    ]

    # Resolve the source formats and drop anything the workflow cannot convert.
    run_files: list[WorkflowRunFile] = []
    for row in rows:
        source_format = extension_from_filename(row.file_name)
        if not source_format:
            problems.append(
                BatchItemResult(
                    file_id=row.id,
                    file_name=row.file_name,
                    error="Could not tell what format this file is.",
                )
            )
            continue
        run_files.append(
            WorkflowRunFile(
                file_id=row.id,
                file_name=row.file_name,
                object_key=row.file_key,
                source_format=source_format,
            )
        )

    # Checked as a whole before anything starts, so the user is told which files
    # will not work rather than discovering it one failure at a time.
    unsupported = workflows.validate_against_files(
        workflow, run_files, supported=get_registry().list_conversions()
    )
    unsupported_ids = {problem.file_id for problem in unsupported}
    for problem in unsupported:
        problems.append(
            BatchItemResult(
                file_id=problem.file_id,
                file_name=problem.file_name or "",
                error=problem.error,
            )
        )

    # The workflow's operations are applied in order, so a multi-step definition
    # produces one pass per distinct target format. Each pass reuses the batch
    # path, and the whole run shares one batch id so its items are grouped.
    batch_id = None
    all_items: list[BatchItemResult] = []
    created_count = 0
    failed_count = 0

    for target_format in workflow.definition.target_formats():
        items = [
            BatchItemRequest(
                file_id=entry.file_id,
                file_name=entry.file_name,
                object_key=entry.object_key,
                source_format=entry.source_format,
                target_format=target_format,
            )
            for entry in run_files
            if entry.file_id not in unsupported_ids
        ]
        if not items:
            continue

        outcome = await conversions.create_batch(
            items=items,
            user_id=current_user.id,
            tier=await _tier_for(subscriptions, current_user.id),
            batch_id=batch_id,
            workflow_id=workflow.workflow_id,
            origin=origin,
        )
        # Every pass of a multi-step workflow shares the first pass's batch id,
        # so "previous runs" counts a run once rather than once per step.
        batch_id = outcome.batch_id
        created_count += outcome.created_count
        failed_count += outcome.failed_count
        all_items.extend(
            BatchItemResult(
                file_id=item.file_id,
                file_name=item.file_name,
                job=_to_response(item.job) if item.job is not None else None,
                error=item.error,
            )
            for item in outcome.outcomes
        )

    failed_count += len(problems)
    if created_count == 0 and failed_count == 0:
        # Every selected file was filtered out before a batch could be formed.
        batch_status = "failed"
    elif failed_count == 0:
        batch_status = "success"
    elif created_count == 0:
        batch_status = "failed"
    else:
        batch_status = "partial"

    return WorkflowRunResponse(
        workflow_id=workflow.workflow_id,
        batch_id=batch_id or "",
        status=batch_status,
        created_count=created_count,
        failed_count=failed_count,
        items=[*problems, *all_items],
        problems=[problem.model_dump() for problem in problems],
    )
