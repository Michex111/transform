from typing import AsyncIterator
from redis.asyncio import Redis


class JobEventPublisher:
    """
    A class responsible for publishing job events to a Redis stream.
    """
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.stream_name = "conversion_job_events"

    async def publish(self, job_id: str, status: str, progress: int, message: str | None = None, **kwargs) -> None:
        event: dict = {
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "message": message or "",
        }
        # Preserve extra event fields (e.g. compute_duration_ms, credits_used)
        event.update({k: str(v) for k, v in kwargs.items() if v is not None})
        await self.redis_client.xadd(self.stream_name, event)


class JobEventSubscriber:
    """
    Reads job events from the Redis stream for a single job.

    Used by the SSE endpoint to stream real-time progress to clients.
    """

    def __init__(self, redis_client: Redis, stream_name: str = "conversion_job_events"):
        self.redis_client = redis_client
        self.stream_name = stream_name

    async def iter_events(self, job_id: str) -> AsyncIterator[tuple[str, dict]]:
        """
        Yield (message_id, event_dict) pairs for a job, replaying existing
        events first and then blocking for new ones.

        The generator ends when a COMPLETED or FAILED event is seen for the job.
        """
        last_id = "0"  # replay history first
        terminal_seen = False

        while not terminal_seen:
            try:
                entries = await self.redis_client.xread(  # type: ignore[arg-type]
                    {self.stream_name: str(last_id)}, count=50, block=5000
                )
            except Exception:
                # Redis unreachable — keep the stream alive and retry.
                entries = []

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
        