"""Tests for the Redis job event subscriber used by the SSE endpoint."""

import asyncio
import time

from src.infrastructure.adapters.queues.redis_stream_status_queue import (
    _EVENT_STREAM_MAXLEN,
    _REPLAY_RETRY_BASE_DELAY,
    JobEventPublisher,
    JobEventSubscriber,
)


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


class FlakyRedisStream:
    """Raises on the first read, then yields a terminal event."""

    def __init__(self, failures: int = 1) -> None:
        self.calls = 0
        self._failures = failures

    async def xread(self, streams, count=1, block=None):
        del streams, count, block
        self.calls += 1
        if self.calls <= self._failures:
            raise ConnectionError("redis is unreachable")
        return [
            (
                "conversion_job_events",
                [("2", {"job_id": "job-1", "status": "COMPLETED", "progress": "100", "message": "done"})],
            )
        ]


def test_iter_events_backs_off_when_redis_is_unavailable() -> None:
    """PERF-4: a Redis failure must not turn this generator into a busy loop.

    The old handler set ``entries = []`` and ``continue``d — no sleep, no log —
    so an unreachable Redis pinned a CPU inside the SSE request task while the
    job context was thrown away.
    """
    redis = FlakyRedisStream()
    subscriber = JobEventSubscriber(redis)  # type: ignore[arg-type]

    async def _run() -> list[str]:
        return [message_id async for message_id, _ in subscriber.iter_events("job-1")]

    started = time.monotonic()
    seen = asyncio.run(_run())
    elapsed = time.monotonic() - started

    assert seen == ["2"]
    # Exactly one retry: a busy loop would have called xread thousands of times.
    assert redis.calls == 2
    # ...and it waited before retrying.
    assert elapsed >= _REPLAY_RETRY_BASE_DELAY * 0.5


class CapturingRedisStream:
    """Records ``xadd`` calls so the published options can be asserted."""

    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict, int | None]] = []

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        del approximate
        self.xadds.append((stream, dict(fields), maxlen))
        return "1-0"


def test_publish_events_bounds_the_stream() -> None:
    """W-19: the event stream had no bound at all.

    ``publish`` appends ~5 entries per job (guest jobs included), so an
    unbounded stream grows forever and can hit the Redis plan's memory cap,
    after which writes start failing.
    """
    redis = CapturingRedisStream()
    publisher = JobEventPublisher(redis)  # type: ignore[arg-type]

    asyncio.run(
        publisher.publish(job_id="job-1", status="PROCESSING", progress=25, message=None)
    )

    assert len(redis.xadds) == 1
    stream, fields, maxlen = redis.xadds[0]
    assert stream == "conversion_job_events"
    assert maxlen == _EVENT_STREAM_MAXLEN
    assert maxlen > 1000  # a job's own events must survive until its client reads them
    assert fields["job_id"] == "job-1"
