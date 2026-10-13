"""The work itself: one execution, or one environment snapshot.

Two rules run through both handlers:

* **The durable row is the source of truth.** The queue message only says "look
  at this row". Everything the handler needs is read from the database, so a
  message that arrives twice, late, or after the run was cancelled produces the
  same answer the API would give.
* **A terminal state is never overwritten.** Before writing an outcome, the row
  is re-read: if the user cancelled while the program was running, the
  cancellation wins and the outcome is discarded. That is the fix for the one
  race this pipeline cannot avoid — a cancel arriving mid-execution.
"""

from typing import Any

from src.application.exceptions.runtime_provider_exceptions import (
    RuntimeProviderError,
)
from src.application.ports.runtime_provider_port import ExecutionRequest
from src.domain.workspaces.entities.execution import ExecutionArtifact
from src.infrastructure.adapters.queues.redis_stream_workspace_queue import (
    WORK_KIND_EXECUTION,
    WORK_KIND_SNAPSHOT,
    WorkspaceWorkMessage,
)
from src.infrastructure.adapters.repository.sql_execution_repo import (
    SQLExecutionRepository,
)
from src.infrastructure.adapters.repository.sql_workspace_repo import (
    SQLRuntimeImageRevisionRepository,
    SQLRuntimeRepository,
    SQLWorkspaceRepository,
)
from src.infrastructure.logging.loggers import worker_logger
import asyncio
from pathlib import Path

#: Default artifact directory, overridden by the worker context's setting.
_ARTIFACT_DIRECTORY = "outputs"

_MAX_ERROR_CHARS = 900


def _describe(exc: BaseException) -> str:
    text = f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__
    return text[:_MAX_ERROR_CHARS]


async def handle_execution(context: Any, message: WorkspaceWorkMessage) -> None:
    """Run one queued execution on its workspace's runtime."""
    execution_id = message.execution_id
    if not execution_id:
        raise ValueError("An execution message carried no execution_id.")

    async with context.session_factory() as session:
        executions = SQLExecutionRepository(session=session)
        workspaces = SQLWorkspaceRepository(session=session)
        service = context.build_service(session)

        execution = await executions.get_execution(execution_id)
        if execution is None:
            # Nothing to run. Not an error: the row may have been deleted with
            # its workspace, and a retry would achieve nothing.
            worker_logger.warning("Execution %s no longer exists; skipping", execution_id)
            return
        if execution.is_terminal():
            # Idempotency: a redelivered message must not run the work twice.
            worker_logger.info(
                "Execution %s is already %s; skipping", execution_id, execution.status
            )
            return

        workspace = await workspaces.get_workspace(
            execution.workspace_id, execution.user_id
        )
        if workspace is None:
            execution.fail(error="The workspace no longer exists.")
            await executions.update_execution(execution)
            return

        # Bring the runtime up through the same service the API uses. Idempotent,
        # so a second queued execution does not allocate a second container.
        started = await service.start_runtime(execution.workspace_id, execution.user_id)
        runtime = started.runtime
        if not runtime.is_serving() or not runtime.provider_ref:
            execution.fail(error=f"The runtime is {runtime.state}; nothing was run.")
            await executions.update_execution(execution)
            worker_logger.warning(
                "Could not start the runtime for execution %s (%s)",
                execution_id,
                runtime.failure_reason or runtime.state,
            )
            return

        # Atomic claim: exactly one worker can move QUEUED -> RUNNING, so a
        # redelivery or a second consumer loses this and returns quietly.
        claimed = await executions.claim_execution(execution_id, runtime.runtime_id)
        if not claimed:
            worker_logger.info(
                "Execution %s was already claimed or cancelled; skipping", execution_id
            )
            return

        execution.start(runtime_id=runtime.runtime_id)

        request = ExecutionRequest(
            mode=execution.mode,
            source=execution.source,
            timeout_seconds=context.execution_timeout_seconds,
            max_output_bytes=context.max_output_bytes,
        )
        try:
            outcome = await context.provider.execute(runtime.provider_ref, request)
        except RuntimeProviderError as exc:
            # Infrastructure, not the user's code. The failed runtime is already
            # recorded by ``start_runtime`` if the provider was unreachable.
            await _persist_outcome(
                executions, execution, error=_describe(exc), exit_code=None
            )
            return

        # A cancellation that arrived while the program was running must win:
        # re-read before writing a terminal state.
        latest = await executions.get_execution(execution_id)
        if latest is not None and latest.is_terminal():
            worker_logger.info(
                "Execution %s finished as %s after the user cancelled it; "
                "keeping the cancellation",
                execution_id,
                latest.status,
            )
            return

        if outcome.timed_out:
            execution.time_out(
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                duration_ms=outcome.duration_ms,
            )
        elif outcome.error:
            execution.fail(
                error=outcome.error,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                duration_ms=outcome.duration_ms,
            )
        else:
            execution.succeed(
                exit_code=outcome.exit_code if outcome.exit_code is not None else -1,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                duration_ms=outcome.duration_ms,
                memory_peak_bytes=outcome.memory_peak_bytes,
                artifacts=await _collect_artifacts(
                    context,
                    runtime.provider_ref,
                    execution.workspace_id,
                    execution_id,
                    outcome.exit_code,
                ),
            )

        await executions.update_execution(execution)
        worker_logger.info(
            "Execution %s finished as %s (exit=%s, duration_ms=%s)",
            execution_id,
            execution.status,
            execution.exit_code,
            execution.duration_ms,
        )


async def _collect_artifacts(
    context: Any, provider_ref: str, workspace_id: str, execution_id: str, exit_code: int | None
):
    """Copy the run's output files out of the runtime and record them.

    WHY copy instead of just recording the path: the runtime is disposable and its
    filesystem is not durable. A path recorded only in the database becomes a
    dangling reference the moment the container is replaced, and the UI would
    offer a download that cannot work. Copying the bytes into the same object
    storage the rest of the product uses is what makes an artifact outlive its
    run — and it reuses the existing bucket, credentials and authorization rather
    than adding a second store.

    Best-effort per file and in total. A failure to copy one artifact logs and
    leaves that artifact with no object key (the API reports it as not
    downloadable) rather than failing a run that already succeeded.
    """
    storage = getattr(context, "storage", None)
    directory = getattr(context, "artifact_directory", _ARTIFACT_DIRECTORY)
    try:
        entries = await context.provider.list_files(provider_ref, directory)
    except Exception as exc:  # noqa: BLE001 - a listing failure is not a run failure
        worker_logger.warning("Could not list workspace artifacts: %s", _describe(exc))
        return []

    files = [entry for entry in entries if not entry.is_dir]
    max_count = getattr(context, "max_artifacts_per_execution", 20)
    max_bytes = getattr(context, "max_artifact_bytes", 25 * 1024 * 1024)
    if len(files) > max_count:
        worker_logger.warning(
            "Execution %s produced %s artifacts; only the first %s are kept",
            execution_id,
            len(files),
            max_count,
        )
        files = files[:max_count]

    artifacts: list[ExecutionArtifact] = []
    for entry in files:
        artifact = ExecutionArtifact(path=entry.path, size_bytes=entry.size_bytes)
        if storage is None:
            # No storage wired: the file is still in the workspace and readable
            # through the files API, so the path is worth recording — it is the
            # *copy* that needs somewhere to go, not the record. The API reports
            # such an artifact as not downloadable rather than as a broken link.
            artifacts.append(artifact)
            continue
        # Read lazily: a context without storage must not need a settings object,
        # or a deployment (or a test) that never copies would crash here instead
        # of simply recording the paths.
        base_key = str(getattr(getattr(context, "settings", None), "BASE_TARGET_KEY", "")).rstrip("/")
        if entry.size_bytes > max_bytes:
            worker_logger.warning(
                "Artifact %s is %s bytes, over the %s-byte limit; not copied",
                entry.path,
                entry.size_bytes,
                max_bytes,
            )
            artifacts.append(artifact)
            continue
        try:
            payload = await context.provider.read_file(provider_ref, entry.path)
        except Exception as exc:  # noqa: BLE001
            worker_logger.warning("Could not read artifact %s: %s", entry.path, _describe(exc))
            artifacts.append(artifact)
            continue
        # The key is namespaced by workspace and execution so two runs of the same
        # script cannot overwrite each other's outputs.
        name = entry.path.rsplit("/", 1)[-1]
        object_key = (
            f"{base_key}/workspace/{workspace_id}/execution/{execution_id}/{name}"
        )
        try:
            await _upload_bytes(context.storage, object_key, payload)
        except Exception as exc:  # noqa: BLE001
            worker_logger.warning(
                "Could not store artifact %s: %s", entry.path, _describe(exc)
            )
            artifacts.append(artifact)
            continue
        artifacts.append(
            ExecutionArtifact(
                path=entry.path,
                size_bytes=len(payload),
                object_key=object_key,
            )
        )
    return artifacts


async def _upload_bytes(storage: Any, object_key: str, payload: bytes) -> None:
    """Upload bytes through a path-based storage gateway.

    The gateway the rest of the worker uses takes ``(key, source_path)``, so the
    bytes are staged in a temp file. A temp file rather than a new storage method
    on purpose: adding an upload-bytes method to the storage port would be a
    second write path through the same adapter for no gain, and the artifacts
    here are bounded to ``max_artifact_bytes`` anyway.
    """
    import tempfile

    def _write_and_upload() -> None:
        with tempfile.NamedTemporaryFile(delete=True) as handle:
            handle.write(payload)
            handle.flush()
            storage.upload(object_key, Path(handle.name))

    await asyncio.to_thread(_write_and_upload)


async def _persist_outcome(
    executions: SQLExecutionRepository,
    execution: Any,
    *,
    error: str,
    exit_code: int | None,
) -> None:
    execution.fail(error=error, exit_code=exit_code)
    await executions.update_execution(execution)


async def handle_snapshot(context: Any, message: WorkspaceWorkMessage) -> None:
    """Build one environment snapshot from the workspace's running runtime."""
    revision_id = message.revision_id
    workspace_id = message.workspace_id
    if not revision_id or not workspace_id:
        raise ValueError("A snapshot message carried no revision_id or workspace_id.")

    async with context.session_factory() as session:
        revisions = SQLRuntimeImageRevisionRepository(session=session)
        runtimes = SQLRuntimeRepository(session=session)

        revision = await revisions.get_revision(revision_id, workspace_id)
        if revision is None:
            worker_logger.warning("Revision %s no longer exists; skipping", revision_id)
            return
        if revision.status.value in ("READY", "FAILED"):
            worker_logger.info(
                "Revision %s is already %s; skipping", revision_id, revision.status
            )
            return

        runtime = await runtimes.get_runtime_for_workspace(workspace_id)
        if runtime is None or not runtime.is_serving() or not runtime.provider_ref:
            # A snapshot of a runtime that is not up is impossible, and saying so
            # is better than leaving the row PENDING forever.
            revision.mark_failed(
                "The runtime is not running, so there is nothing to snapshot."
            )
            await revisions.update_revision(revision)
            return

        revision.mark_building()
        await revisions.update_revision(revision)

        try:
            snapshot_ref = await context.provider.create_snapshot(
                runtime.provider_ref,
                workspace_id=workspace_id,
                revision_id=revision_id,
            )
        except RuntimeProviderError as exc:
            revision.mark_failed(_describe(exc))
            await revisions.update_revision(revision)
            worker_logger.warning(
                "Snapshot %s failed: %s", revision_id, revision.error
            )
            return

        revision.mark_ready(
            provider_snapshot_ref=snapshot_ref,
            metadata={"provider": context.provider.name},
        )
        await revisions.update_revision(revision)
        worker_logger.info("Snapshot %s is ready at %s", revision_id, snapshot_ref)

        await _prune_revisions(context, revisions, workspace_id)


async def _prune_revisions(
    context: Any, revisions: SQLRuntimeImageRevisionRepository, workspace_id: str
) -> None:
    """Keep only the newest ``snapshot_retention`` READY revisions.

    Snapshots are images; without a bound, a workspace used daily for a year
    would accumulate backups nobody will ever restore and slowly fill the host's
    image store. FAILED revisions are kept — they are the record of what was
    attempted, and they are tiny (a row, not an image).
    """
    try:
        existing = await revisions.list_revisions(
            workspace_id, limit=context.snapshot_retention + 20
        )
    except Exception as exc:  # noqa: BLE001 - pruning must never fail the snapshot
        worker_logger.warning("Could not list revisions for pruning: %s", _describe(exc))
        return

    ready = [item for item in existing if item.status.value == "READY"]
    for stale in ready[context.snapshot_retention :]:
        try:
            await revisions.delete_revision(stale.revision_id, workspace_id)
            worker_logger.info("Pruned old revision %s", stale.revision_id)
        except Exception as exc:  # noqa: BLE001
            worker_logger.warning("Could not prune revision %s: %s", stale.revision_id, exc)


async def process_work(context: Any, message: WorkspaceWorkMessage) -> None:
    """Dispatch one message. An unknown kind is an error worth dead-lettering."""
    if message.kind == WORK_KIND_EXECUTION:
        await handle_execution(context, message)
        return
    if message.kind == WORK_KIND_SNAPSHOT:
        await handle_snapshot(context, message)
        return
    raise ValueError(f"Unknown workspace work kind {message.kind!r}.")
