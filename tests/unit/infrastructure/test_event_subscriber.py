"""Tests for the Redis job event subscriber used by the SSE endpoint."""

import asyncio

from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventSubscriber


class FakeRedisStream:
    """Minimal in-memory stand-in for the redis.asyncio xread/xrevrange API."""

    def __init__(self, events: list[tuple[str, dict]]):
        # (message_id, fields)
        self._events = events

    async def xread(self, streams, count=1, block=None):
        """Replay entries with id > the given last_id (blocking ignored)."""
        stream, last_id = next(iter(streams.items()))
        if last_id == "$":
            return []
        offset = 0
        if last_id != "0":
            # find index of last_id
            for idx, (mid, _) in enumerate(self._events):
                if mid == last_id:
                    offset = idx + 1
                    break
        remaining = self._events[offset:offset + count]
        return [(stream, remaining)]

    async def xrevrange(self, stream, count=None):
        if count is None:
            return list(reversed(self._events))
        return list(reversed(self._events[-count:]))


def test_iter_events_replays_history_and_stops_at_terminal() -> None:
    events = [
        ("1", {"job_id": "job-1", "status": "PROCESSING", "progress": "25", "message": "downloading file"}),
        ("2", {"job_id": "job-1", "status": "COMPLETED", "progress": "100", "message": "conversion completed"}),
    ]
    subscriber = JobEventSubscriber(FakeRedisStream(events))  # type: ignore[arg-type]

    async def _run() -> list[tuple[str, dict]]:
        seen: list[tuple[str, dict]] = []
        async for message_id, fields in subscriber.iter_events("job-1"):
            seen.append((message_id, fields))
        return seen

    seen = asyncio.run(_run())
    assert [mid for mid, _ in seen] == ["1", "2"]
    assert seen[-1][1]["status"] == "COMPLETED"


def test_iter_events_filters_other_jobs() -> None:
    events = [
        ("1", {"job_id": "other", "status": "PROCESSING", "progress": "50", "message": ""}),
        ("2", {"job_id": "job-2", "status": "FAILED", "progress": "0", "message": "boom"}),
    ]
    subscriber = JobEventSubscriber(FakeRedisStream(events))  # type: ignore[arg-type]

    async def _run() -> list[tuple[str, dict]]:
        seen: list[tuple[str, dict]] = []
        async for message_id, fields in subscriber.iter_events("job-2"):
            seen.append((message_id, fields))
        return seen

    seen = asyncio.run(_run())
    assert [mid for mid, _ in seen] == ["2"]
    assert seen[0][1]["status"] == "FAILED"


def test_latest_event_returns_most_recent_for_job() -> None:
    events = [
        ("1", {"job_id": "job-1", "status": "PROCESSING", "progress": "25", "message": ""}),
        ("2", {"job_id": "job-2", "status": "PROCESSING", "progress": "50", "message": ""}),
        ("3", {"job_id": "job-1", "status": "COMPLETED", "progress": "100", "message": "done"}),
    ]
    subscriber = JobEventSubscriber(FakeRedisStream(events))  # type: ignore[arg-type]

    latest = asyncio.run(subscriber.latest_event("job-1"))
    assert latest is not None
    assert latest["status"] == "COMPLETED"
