import asyncio
import logging
from typing import AsyncIterator
from redis.asyncio import Redis

from .stream_names import JOB_EVENT_STREAM, qualify

logger = logging.getLogger(__name__)

# Approximate cap on retained job events. The stream had no bound at all while
# ``publish`` appends ~5 entries per job (guest jobs included), so it grew
# forever and could eventually hit the Redis plan's memory cap and start
# rejecting writes. ~100k entries is weeks of history at current volumes — far
# more than any connected SSE client needs — and the subscriber tolerates a
# late connect because the client falls back to GET /jobs/{id} and the stream
# emits heartbeats. It is deliberately not so small that a long-running job's
# own events could be evicted before its client reads them.
_EVENT_STREAM_MAXLEN = 100_000

# Bounded backoff for the SSE replay loop when Redis is unreachable.
_REPLAY_RETRY_BASE_DELAY = 0.5
_REPLAY_RETRY_MAX_DELAY = 10.0


class JobEventPublisher:
    """
    A class responsible for publishing job events to a Redis stream.
    """
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        # Namespaced per environment so a dev worker's events are not read by
        # the production SSE endpoint (and vice versa). Empty prefix = the
        # original production name.
        self.stream_name = qualify(JOB_EVENT_STREAM)

    async def publish(self, job_id: str, status: str, progress: int, message: str | None = None, **kwargs) -> None:
        event: dict = {
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "message": message or "",
        }
        # Preserve extra event fields (e.g. compute_duration_ms, credits_used)
        event.update({k: str(v) for k, v in kwargs.items() if v is not None})
        await self.redis_client.xadd(
            self.stream_name, event, maxlen=_EVENT_STREAM_MAXLEN
        )


class JobEventSubscriber:
    """
    Reads job events from the Redis stream for a single job.

    Used by the SSE endpoint to stream real-time progress to clients.
    """

    def __init__(self, redis_client: Redis, stream_name: str | None = None):
        self.redis_client = redis_client
        # Default to the environment-qualified name; an explicit name is still
        # honoured (tests and replay tools pass one).
        self.stream_name = stream_name or qualify(JOB_EVENT_STREAM)

    async def iter_events(self, job_id: str) -> AsyncIterator[tuple[str, dict]]:
        """
        Yield (message_id, event_dict) pairs for a job, replaying existing
        events first and then blocking for new ones.

        The generator ends when a COMPLETED or FAILED event is seen for the job.
        """
        last_id = "0"  # replay history first
        terminal_seen = False
        backoff = _REPLAY_RETRY_BASE_DELAY

        while not terminal_seen:
            try:
                entries = await self.redis_client.xread(  # type: ignore[arg-type]
                    {self.stream_name: str(last_id)}, count=50, block=5000
                )
                backoff = _REPLAY_RETRY_BASE_DELAY  # recovered — reset the backoff
            except Exception as e:  # noqa: BLE001
                # Redis unreachable. This used to ``continue`` immediately, a
                # tight loop that burned a full CPU inside the SSE request task.
                # Back off (bounded, so recovery is prompt) and log with the job
                # context instead of spinning silently.
                logger.warning(
                    "SSE event replay for job %s failed (%s); retrying in %.1fs",
                    job_id, e, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _REPLAY_RETRY_MAX_DELAY)
                continue

            if not entries:
                continue

            _, messages = entries[0]  # type: ignore[index]
            for message_id, fields in messages:  # type: ignore[union-attr]
                raw = dict(fields)  # type: ignore[arg-type]
                fields_dict = {str(k): str(v) for k, v in raw.items()}
                # Always advance the cursor past every consumed message (matching
                # or not) so a busy stream full of other jobs' events does not
                # make us re-read the same non-matching entries in a loop.
                last_id = str(message_id)
                if fields_dict.get("job_id") != job_id:
                    continue
                yield str(message_id), fields_dict
                if fields_dict.get("status") in ("COMPLETED", "FAILED"):
                    terminal_seen = True
                    break

            if terminal_seen:
                break

    async def latest_event(self, job_id: str) -> dict | None:
        """Return the most recent event for a job, or None."""
        try:
            entries = await self.redis_client.xrevrange(  # type: ignore[arg-type]
                self.stream_name, count=50
            )
        except Exception:
            return None
        for message_id, fields in entries:  # type: ignore[union-attr]
            raw = dict(fields)  # type: ignore[arg-type]
            fields_dict = {str(k): str(v) for k, v in raw.items()}
            if fields_dict.get("job_id") == job_id:
                return fields_dict
        return None
        