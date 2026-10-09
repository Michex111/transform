import logging
from dataclasses import dataclass, field
from typing import Sequence
from uuid import uuid4

from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.ports.database_port import ConversionJobRepositoryPort
from src.application.ports.queue_port import JobQueuePort
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.policies.conversion_policy import is_supported
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.converters.converter_registry import get_registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BatchItemRequest:
    """One file to convert, already resolved and authorized by the caller.

    ``object_key`` and ``source_format`` are resolved from the file record
    *before* the batch starts (see the router), so this layer needs no
    knowledge of the file repository and the ownership check has already
    happened by the time a job is created.
    """

    file_id: str
    file_name: str
    object_key: str
    source_format: str
    target_format: str


@dataclass
class BatchItemOutcome:
    """What happened to one item: either a job, or a reason there is not one."""

    file_id: str
    file_name: str
    job: ConversionJob | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.job is not None


@dataclass
class BatchOutcome:
    """The result of a batch request, per item."""

    batch_id: str
    outcomes: list[BatchItemOutcome] = field(default_factory=list)
    created_count: int = 0
    failed_count: int = 0
    workflow_id: str | None = None

    @property
    def status(self) -> str:
        """The batch's own outcome, derived from its items rather than stored.

        ``success`` / ``partial`` / ``failed`` are computed on every read, so
        they cannot disagree with the jobs they describe.
        """
        if self.failed_count == 0:
            return "success"
        if self.created_count == 0:
            return "failed"
        return "partial"



class ConversionService:
    def __init__(
        self,
        queue_port: JobQueuePort,
        db_repository: ConversionJobRepositoryPort,
        queue_dispatcher: PriorityQueueDispatcher | None = None,
    ):
        self.queue_port = queue_port
        self.db_repository = db_repository
        self.queue_dispatcher = queue_dispatcher

    async def create_conversion_job(self, job: ConversionJob) -> str:
        """Validate the conversion type, assign an ID, and persist the job."""
        is_supported(job.conversion, get_registry().list_conversions())
        job.job_id = str(uuid4())
        await self.db_repository.save_conversion_job(job)
        return job.job_id

    async def push_conversion_job(
        self,
        job: ConversionJob,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> str:
        """Enqueue an already-persisted job for processing.

        The job must already have a ``job_id`` assigned. Its status is moved
        from ``AWAITING_UPLOAD`` to ``PENDING`` before it is published so that
        workers can immediately start processing it.
        """
        if not job.job_id:
            raise InvalidConversionJobError(
                "Job ID must be set before pushing the job to the queue."
            )

        is_supported(job.conversion, get_registry().list_conversions())

        # Persist the PENDING state transition before publishing
        if job.status.value == "AWAITING_UPLOAD":
            job.pending_processing()
            await self.db_repository.update_conversion_job(job)

        if self.queue_dispatcher is not None:
            await self.queue_dispatcher.dispatch(job, tier=tier)
        else:
            await self.queue_port.publish_job(job)
        return job.job_id

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> ConversionJob:
        """Create and enqueue a job that converts a file already stored in the
        user's library.

        Unlike the upload flow, the input object is already persisted at
        ``object_key``, so the job is created directly and enqueued immediately
        (no separate upload/verify step). The worker downloads the existing
        object and converts it.

        ``origin`` records how the request authenticated so the worker can pick
        the right credit spend order; it defaults to ``WEB`` for callers (such
        as the assistant tool) that do not thread the request's credential down
        to this layer.

        Raises:
            InvalidConversion: If ``source_format`` / ``target_format`` is not
                a supported conversion.
        """
        job = ConversionJob(
            job_id="",
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=file_name,
            object_key=object_key,
            user_id=user_id,
            origin=origin,
        )
        await self.create_conversion_job(job)
        await self.push_conversion_job(job, tier=tier)
        return job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return await self.db_repository.get_conversion_job(job_id)

    async def list_history(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        since=None,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's conversion history (newest first) plus total count.

        Args:
            since: Optional datetime; only jobs created on/after this time are
                returned (used for the time-range filter on the History page).
        """
        return await self.db_repository.list_user_history(user_id, offset, limit, since=since)

    async def search_jobs(
        self,
        user_id: int,
        *,
        query: str | None = None,
        fmt: str | None = None,
        limit: int = 10,
    ) -> list[ConversionJob]:
        """Find the user's jobs by a name substring and/or a format.

        The natural-language counterpart to :meth:`list_history`: "the homework
        one I converted" has no offset and no total, just the jobs that match,
        newest first. Ownership is the repository's ``user_id`` scope, so the
        assistant cannot widen it.
        """
        return await self.db_repository.search_jobs(
            user_id, query=query, fmt=fmt, limit=limit
        )

    async def delete_history_job(self, job_id: str, user_id: int) -> bool:
        """Delete a single conversion-history record owned by ``user_id``.

        Returns:
            True if a job was deleted; False if it was not found or not owned.
        """
        return await self.db_repository.delete_job(job_id, user_id)

    async def preview_history_delete(
        self, user_id: int, *, since=None
    ) -> tuple[int, int]:
        """Count what :meth:`delete_history_range` would remove.

        Read-only, and deliberately routed through the same repository
        predicate as the delete so the confirmation dialog and the outcome can
        never disagree.

        Args:
            since: Optional lower bound on ``created_at`` (None means all time).
                Callers must derive it from an explicit, validated range — never
                from an absent parameter, which must not be able to mean
                "everything".

        Returns:
            ``(deletable, active)`` — jobs that would be deleted, and jobs
            inside the same window that are still running and would be kept.
        """
        return await self.db_repository.count_deletable_history(user_id, since)

    async def delete_history_range(
        self, user_id: int, *, since=None
    ) -> tuple[int, int]:
        """Delete the user's terminal jobs inside a time window.

        Never deletes a job that is still running (PENDING/PROCESSING/
        AWAITING_UPLOAD): a row removed from under a worker orphans the job and
        loses its history entry. Those jobs are reported back instead.

        Stored objects (the input and output files in bucket storage) are **not**
        deleted — the cleanup worker owns them and reclaims them on its normal
        retention sweep. Deleting the references synchronously would have to
        enumerate an unbounded number of objects while the user waits, and a
        partial failure would leave rows that point at nothing.

        Returns:
            ``(deleted_count, skipped_active)``.
        """
        return await self.db_repository.delete_history_range(user_id, since)

    async def update_conversion_job(self, job: ConversionJob) -> None:
        """Persist progress updates for an existing job (status, output, errors,
        compute time and credits consumed).
        """
        await self.db_repository.update_conversion_job(job)

    async def retry_conversion_job(
        self,
        job_id: str,
        user_id: int | None,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> ConversionJob:
        """Re-enqueue a failed job without re-uploading its input file.

        The input object is already stored at ``object_key``, so retrying only
        resets the job to PENDING and pushes it back to the queue. The worker
        downloads the existing object and tries again.

        Raises:
            InvalidConversionJobError: If the job does not exist, is not owned
                by the caller, or is not in a retryable (FAILED) state.
        """
        job = await self.db_repository.get_conversion_job(job_id)
        if not is_job_owner(job, user_id):
            raise InvalidConversionJobError("Job not found")

        # Reset FAILED -> PENDING (keeps object_key so the existing file is reused).
        job.retry()
        await self.db_repository.update_conversion_job(job)

        # Re-enqueue with the same object_key.
        if self.queue_dispatcher is not None:
            await self.queue_dispatcher.dispatch(job, tier=tier)
        else:
            await self.queue_port.publish_job(job)
        return job

    async def list_batch(self, batch_id: str, user_id: int) -> list[ConversionJob]:
        """Every job a batch created, scoped to its owner.

        An unknown or another account's batch id yields an empty list rather
        than an error: the caller cannot distinguish "no such batch" from "not
        yours" that way, which is the same non-disclosure the rest of the
        conversion API keeps.
        """
        return await self.db_repository.list_by_batch(batch_id, user_id)

    async def count_workflow_runs(self, workflow_ids: list[str], user_id: int) -> dict[str, int]:
        """Run counts per workflow, for the workflow list's badges."""
        return await self.db_repository.count_batches_for_workflows(workflow_ids, user_id)

    async def list_workflow_run_batch_ids(
        self, workflow_id: str, user_id: int, *, limit: int = 20
    ) -> list[str]:
        """The batch ids a workflow produced, newest run first."""
        return await self.db_repository.list_batch_ids_for_workflow(
            workflow_id, user_id, limit=limit
        )

    async def create_batch(
        self,
        *,
        items: Sequence[BatchItemRequest],
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
        batch_id: str | None = None,
        workflow_id: str | None = None,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> BatchOutcome:
        """Create and enqueue one conversion job per item.

        This is the whole of "batch conversion": the group is a ``batch_id``
        stamped on independent jobs, not a new kind of job. Everything that
        already works for one conversion therefore keeps working per item —
        progress streaming, retry, download, history, credits — with nothing to
        re-implement and no second lifecycle to keep in step.

        Per-item outcomes, not all-or-nothing. One unsupported file (or one the
        caller does not own) fails *that item* and leaves the rest to run, which
        is what makes partial success a first-class result instead of a
        rollback. The caller gets a per-item reason it can show against the row
        that failed.

        **Sequential, deliberately.** The obvious implementation is an
        ``asyncio.gather`` over a semaphore, and it is wrong here: the
        repositories share one ``AsyncSession`` per request, and a SQLAlchemy
        session is a single connection with a single transaction. Using it from
        concurrent tasks raises "this session is provisioning a new connection;
        concurrent operations are not permitted" — and it does so
        *intermittently*, only for batches of more than one file, which is
        exactly the shape of bug that reaches production. Bounding the fan-out
        to one is the only correct choice for this session model.

        That is not a loss. The work here is a DB write and a queue publish, so
        the parallelism that matters is the *workers* draining the queue, which
        the tier stream already governs. What must not happen is one request
        blocking on N conversions to finish — and it does not: this returns as
        soon as every job is enqueued, leaving the conversions to run
        asynchronously and be observed on the per-job progress stream.
        """
        effective_batch_id = batch_id or str(uuid4())

        outcomes = [
            await self._create_batch_item(
                item,
                user_id=user_id,
                tier=tier,
                batch_id=effective_batch_id,
                workflow_id=workflow_id,
                origin=origin,
            )
            for item in items
        ]

        created = [outcome for outcome in outcomes if outcome.job is not None]
        return BatchOutcome(
            batch_id=effective_batch_id,
            outcomes=outcomes,
            created_count=len(created),
            failed_count=len(outcomes) - len(created),
            workflow_id=workflow_id,
        )

    async def _create_batch_item(
        self,
        item: BatchItemRequest,
        *,
        user_id: int,
        tier: SubscriptionTier,
        batch_id: str,
        workflow_id: str | None,
        origin: JobOrigin,
    ) -> BatchItemOutcome:
        """Create one batch item's job, turning every failure into an outcome.

        Deliberately total: it never raises. A raise would lose the per-item
        reason the batch response is built on, and would leave the other items
        unaccounted for. Returning an outcome keeps the batch's failure model
        honest: every item is reported, whatever went wrong.
        """
        try:
            job = await self.convert_library_file(
                file_name=item.file_name,
                source_format=item.source_format,
                target_format=item.target_format,
                object_key=item.object_key,
                user_id=user_id,
                tier=tier,
                origin=origin,
            )
        except (InvalidConversion, InvalidConversionJobError) as exc:
            return BatchItemOutcome(file_id=item.file_id, file_name=item.file_name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - see the docstring
            # Anything else (a queue outage, a transient DB error) is reported
            # against this item rather than taking down the batch. The message is
            # kept generic: a driver-level string can carry connection details.
            logger.warning("Batch item %s failed to enqueue: %s", item.file_id, exc)
            return BatchItemOutcome(
                file_id=item.file_id,
                file_name=item.file_name,
                error="Could not start this conversion. Please try again.",
            )

        # Stamp the grouping columns after creation: `convert_library_file` is
        # shared with the single-conversion path and stays unaware of batches.
        job.batch_id = batch_id
        job.workflow_id = workflow_id
        await self.update_conversion_job(job)
        return BatchItemOutcome(
            file_id=item.file_id, file_name=item.file_name, job=job
        )


