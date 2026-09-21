import asyncio
import time

from src.infrastructure.adapters.queues.redis_stream_job_queue import (
    MAX_BUFFERED_MESSAGES_PER_FETCH,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import JobProcess

settings = get_settings()

# How often to sweep for stale pending messages left by crashed workers.
_STALE_SWEEP_INTERVAL_SECONDS = 60
# Slack added on top of the worst-case in-flight time so a worker that is a
# moment away from ACKing a finished job is never mistaken for a dead one.
_STALE_IDLE_MARGIN_MS = 60_000


def stale_min_idle_ms() -> int:
    """Idle time after which a pending message is treated as abandoned.

    A message's idle clock starts when it is *delivered*, which happens before
    the job ahead of it finishes. The threshold therefore has to cover the
    conversion timeout for each message this consumer can be holding at once:

    * 1 for the job currently being processed, plus
    * ``MAX_BUFFERED_MESSAGES_PER_FETCH`` for entries a single ``xreadgroup``
      delivered into our PEL that are still queued behind it.

    If the threshold were just the conversion timeout, a legitimately running
    long conversion would be "reclaimed" by an idle peer, converted twice and —
    because credits are consumed after a successful upload — charged twice.
    Recovery of a genuinely crashed job is correspondingly slower, which is the
    accepted trade-off for never double-charging.

    Known residual limitation: this budgets the *conversion* stage for each
    held message. A job whose object-storage phases also run to their own
    timeouts (see ``processor._STORAGE_OP_TIMEOUT_SECONDS``, each retried up to
    3 times) can take longer than one budget, so in that pathological case the
    total backlog can still exceed the threshold. Eliminating it entirely means
    not holding delivered-but-unprocessed entries at all (re-queueing them, or
    a self-refresh that runs during processing, which the fetch loop cannot do).
    """
    timeout_seconds = int(getattr(settings, "WORKER_CONVERSION_TIMEOUT", 600))
    in_flight_jobs = 1 + MAX_BUFFERED_MESSAGES_PER_FETCH
    return timeout_seconds * 1000 * in_flight_jobs + _STALE_IDLE_MARGIN_MS


class ConverterWorker:
    def __init__(self, context: WorkerContext, process_job: JobProcess):
        self.context = context
        self.process_job = process_job
        self._running = False

    async def run(self):
        self._running = True
        log_context = self.context.get_log_context()
        worker_logger.info("Converter worker started", extra=log_context)
        last_sweep = time.monotonic()

        while self._running:
            try:
                job = await self.context.queue_port.fetch_job()
                if job is None:
                    # Periodically reclaim messages that were left in the
                    # consumer group by a crashed worker: ``reclaim_stale_jobs``
                    # re-queues them so this read path delivers them again,
                    # which is what stops a crashed job from being stuck
                    # forever with no terminal event.
                    if time.monotonic() - last_sweep > _STALE_SWEEP_INTERVAL_SECONDS:
                        try:
                            await self.context.queue_port.reclaim_stale_jobs(
                                min_idle_ms=stale_min_idle_ms()
                            )
                        except Exception as reclaim_error:
                            worker_logger.warning(
                                f"Stale-job reclaim failed: {reclaim_error}", extra=log_context
                            )
                        last_sweep = time.monotonic()
                    await asyncio.sleep(1)  # Sleep briefly if no job is available
                    continue
                
                message_id, job = job
                job_log_context = self.context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
                worker_logger.info(f"Fetched job {job.job_id} for processing", extra=job_log_context)
                try:
                    await self.process_job(self.context, job)
                except Exception as e:
                    # Dead-letter FIRST, then ACK. ``fail_job`` is a bare XACK,
                    # so ACKing first would remove the message from the group
                    # before any copy exists — a failing DLQ write would then
                    # lose the job entirely. Each call is isolated so a Redis
                    # error in one never skips the other, and neither can raise
                    # out of the failure handler (which would abort the loop).
                    error_message = str(e)
                    try:
                        await self.context.queue_port.dead_letter_job(message_id, error_message, job)
                    except Exception as dlq_error:
                        worker_logger.error(
                            f"Failed to dead-letter job {job.job_id}: {dlq_error}",
                            extra=job_log_context,
                        )
                    try:
                        await self.context.queue_port.fail_job(message_id, error_message)
                    except Exception as ack_error:
                        # The message stays pending; the stale sweep re-queues
                        # it rather than dropping it silently.
                        worker_logger.error(
                            f"Failed to acknowledge failed job {job.job_id}: {ack_error}",
                            extra=job_log_context,
                        )
                    worker_logger.error(f"Error processing job {job.job_id}: {error_message}", extra=job_log_context)
                    continue
                else:
                    await self.context.queue_port.acknowledge_job(message_id)
                    worker_logger.info(f"Job {job.job_id} completed successfully", extra=job_log_context)
            except asyncio.CancelledError:
                worker_logger.info("Converter worker received shutdown signal", extra=log_context)
                self.stop()
                # Re-raise: swallowing cancellation leaves the caller (and the
                # event loop) believing shutdown completed while the task was
                # still awaited. Cleanup above is done, so propagate.
                raise
            except Exception as loop_error:
                worker_logger.critical(f"Unexpected error in worker loop: {str(loop_error)}", extra=log_context)
                await asyncio.sleep(5)  # Sleep before retrying to avoid tight error loop
    
    def stop(self):
        self._running = False
        log_context = self.context.get_log_context()
        worker_logger.info("Converter worker stopped", extra=log_context)
            
        
            

