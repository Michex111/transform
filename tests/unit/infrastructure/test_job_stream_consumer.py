"""Tests for the real ``JobStreamConsumer``.

There was no coverage for this class at all before this file. The shared
``FakeQueuePort`` cannot model a pending-entries list, so the three
message-loss defects it hides were invisible:

* a message claimed from a dead consumer was never redelivered (W-1),
* entries a single ``XREADGROUP`` returned for streams after the first were
  dropped on the floor (W-3),
* an ACK was routed by a message id that is only unique within a stream (W-6).

The stub below models just the Redis Streams surface the consumer uses
(entries, per-group pending entries, delivery timestamps, XAUTOCLAIM) so those
paths can be exercised without a live Redis.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.infrastructure.adapters.queues.redis_stream_job_queue as queue_module
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.queues.redis_stream_job_queue import (
    JobStream,
    JobStreamConsumer,
)


def _fields(job_id: str, **overrides: object) -> dict:
    """Stream fields in the shape the API producer publishes."""
    fields: dict = {
        "job_id": job_id,
        "source_format": "pdf",
        "target_format": "docx",
        "input_key": f"{job_id}.pdf",
        "object_key": f"upload/{job_id}.pdf",
        "user_id": "42",
    }
    fields.update(overrides)
    return fields


def _job(job_id: str = "job-1") -> ConversionJob:
    return ConversionJob(
        job_id=job_id,
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file=f"{job_id}.pdf",
        object_key=f"upload/{job_id}.pdf",
    )


class StubRedisStream:
    """In-memory model of the Redis Streams calls ``JobStreamConsumer`` makes.

    Models per-stream entries, per-group pending entries and delivery
    timestamps — the minimum needed for XADD / XREADGROUP / XACK / XAUTOCLAIM.
    ``now`` is a millisecond clock (Redis reports idle time in milliseconds) so
    a test can simulate an elapsed window without sleeping.
    """

    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, dict]]] = {}
        # stream -> message id -> [consumer name, delivered_at_ms]
        self.pending: dict[str, dict[str, list]] = {}
        self.acked: list[tuple[str, str, str]] = []
        self.xadds: list[tuple[str, dict, int | None]] = []
        self.now = 0.0
        self._next_id = 0
        # When True a read returns entries for only the first non-empty stream.
        # Used to isolate the id-collision defect (W-6) from the multi-stream
        # drop (W-3).
        self.one_stream_per_read = False
        # ``describe_endpoint`` reads this shape off a real redis client.
        self.connection_pool = SimpleNamespace(
            connection_kwargs={"host": "redis.test", "port": 6379, "db": 0}
        )

    # ------------------------------------------------------------------
    # test helpers
    # ------------------------------------------------------------------
    def add(self, stream: str, fields: dict, message_id: str | None = None) -> str:
        """Seed an entry. ``message_id`` can be forced to make ids collide."""
        if message_id is None:
            self._next_id += 1
            message_id = f"{self._next_id}-0"
        self.entries.setdefault(stream, []).append((message_id, dict(fields)))
        self.pending.setdefault(stream, {})
        return message_id

    def _fields_for(self, stream: str, message_id: str) -> dict:
        for candidate_id, fields in self.entries.get(stream, []):
            if candidate_id == message_id:
                return dict(fields)
        return {}

    # ------------------------------------------------------------------
    # redis API used by the consumer
    # ------------------------------------------------------------------
    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        del approximate
        message_id = self.add(stream, fields)
        self.xadds.append((stream, dict(fields), maxlen))
        return message_id

    async def xgroup_create(self, stream, group, id="0", mkstream=False):
        del group, id, mkstream
        self.entries.setdefault(stream, [])
        self.pending.setdefault(stream, {})
        return True

    async def xreadgroup(self, group, consumer, streams, count=None, block=None):
        del group, block
        delivered = []
        for stream, last_id in streams.items():
            if last_id != ">":
                continue  # the consumer only ever asks for new messages
            pending = self.pending.setdefault(stream, {})
            candidates = [
                (message_id, fields)
                for message_id, fields in self.entries.get(stream, [])
                if message_id not in pending
            ]
            selected = candidates[:count] if count else candidates
            for message_id, _ in selected:
                pending[message_id] = [consumer, self.now]
            if selected:
                # NOTE: one reply element per stream, matching Redis' per-stream
                # (not global) COUNT semantics.
                delivered.append((stream, selected))
                if self.one_stream_per_read:
                    break
        return delivered or None

    async def xack(self, stream, group, message_id):
        removed = 1 if self.pending.get(stream, {}).pop(message_id, None) is not None else 0
        self.acked.append((stream, group, message_id))
        return removed

    async def xautoclaim(
        self, stream, group, consumer, min_idle_time=None, start_id=None, count=None
    ):
        del group, start_id
        claimed = []
        for message_id, entry in self.pending.get(stream, {}).items():
            owner, delivered_at = entry
            del owner
            if self.now - delivered_at < (min_idle_time or 0):
                continue
            if count is not None and len(claimed) >= count:
                break
            entry[0] = consumer
            entry[1] = self.now  # a claim resets the idle clock
            claimed.append((message_id, self._fields_for(stream, message_id)))
        return ["0-0", claimed, []]


class CapturingLogger:
    """Minimal ``worker_logger`` stand-in that records formatted messages."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, message: str, args: tuple) -> None:
        self.records.append((level, message % args if args else message))

    def info(self, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        del kwargs
        self._record("info", message, args)

    def warning(self, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        del kwargs
        self._record("warning", message, args)

    def error(self, message, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        del kwargs
        self._record("error", message, args)


# ----------------------------------------------------------------------
# W-3 — one XREADGROUP can return entries for several streams
# ----------------------------------------------------------------------


def test_fetch_job_returns_every_stream_a_single_read_delivered() -> None:
    """COUNT is per-stream, so one read can deliver an entry per stream.

    Only ``response[0]`` was consumed before, so the entry for every stream
    after the first was delivered into our PEL and then discarded — and `'>'`
    never returns it again, which is silent job loss.
    """
    redis = StubRedisStream()
    redis.add("conversion_jobs:high", _fields("high-job"))
    redis.add("conversion_jobs:low", _fields("low-job"))
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)

    first = asyncio.run(consumer.fetch_job())
    second = asyncio.run(consumer.fetch_job())

    assert first is not None and second is not None
    # Redis returns replies in the order the streams were requested, so the
    # buffered entry is handed out in priority order.
    assert [first[1].job_id, second[1].job_id] == ["high-job", "low-job"]
    assert asyncio.run(consumer.fetch_job()) is None

    asyncio.run(consumer.acknowledge_job(first[0]))
    asyncio.run(consumer.acknowledge_job(second[0]))

    assert [(stream, message_id) for stream, _, message_id in redis.acked] == [
        ("conversion_jobs:high", "1-0"),
        ("conversion_jobs:low", "2-0"),
    ]
    # Both entries left the group's pending list.
    assert redis.pending["conversion_jobs:high"] == {}
    assert redis.pending["conversion_jobs:low"] == {}


# ----------------------------------------------------------------------
# W-6 — stream ids are unique only within a stream
# ----------------------------------------------------------------------


def test_ack_is_routed_to_the_stream_the_message_came_from() -> None:
    """Two streams can mint the same literal id in the same millisecond.

    Keying deliveries by the bare id let the second overwrite the first, so the
    first job's XACK went to the other stream (returning 0 without raising) and
    the first job stayed pending while the code believed it was acknowledged.
    """
    redis = StubRedisStream()
    redis.one_stream_per_read = True
    redis.add("conversion_jobs:high", _fields("job-a"), message_id="5-0")
    redis.add("conversion_jobs:low", _fields("job-b"), message_id="5-0")
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)

    first = asyncio.run(consumer.fetch_job())
    second = asyncio.run(consumer.fetch_job())
    assert first is not None and second is not None
    assert [first[1].job_id, second[1].job_id] == ["job-a", "job-b"]
    # The delivery keys are unambiguous even though the stream ids are not.
    assert first[0] != second[0]

    asyncio.run(consumer.acknowledge_job(first[0]))
    asyncio.run(consumer.acknowledge_job(second[0]))

    assert redis.acked == [
        ("conversion_jobs:high", "conversion-workers", "5-0"),
        ("conversion_jobs:low", "conversion-workers", "5-0"),
    ]
    assert redis.pending["conversion_jobs:high"] == {}
    assert redis.pending["conversion_jobs:low"] == {}


# ----------------------------------------------------------------------
# W-1 — a claimed stale message must actually be redelivered
# ----------------------------------------------------------------------


def test_reclaim_stale_jobs_redelivers_a_job_abandoned_by_a_crashed_worker() -> None:
    """A message read but never ACKed used to be stuck forever.

    ``reclaim_stale_jobs`` claimed it into this consumer's PEL and logged
    success, but nothing ever read the PEL, so the job's row stayed PROCESSING
    with no terminal event.
    """
    redis = StubRedisStream()
    redis.add("conversion_jobs:normal", _fields("crashed-job", user_id="7"))
    crashed = JobStreamConsumer("conversion-workers", "worker-dead", redis)

    delivered = asyncio.run(crashed.fetch_job())
    assert delivered is not None
    # The consumer dies here: no ACK, so the entry stays in the group's PEL.
    assert list(redis.pending["conversion_jobs:normal"]) == ["1-0"]

    redis.now += 3_600_000  # an hour passes before another worker sweeps

    live = JobStreamConsumer("conversion-workers", "worker-live", redis)
    assert asyncio.run(live.reclaim_stale_jobs(min_idle_ms=60_000)) == 1

    redelivered = asyncio.run(live.fetch_job())
    assert redelivered is not None
    job = redelivered[1]
    # Everything the processor needs survives the round trip.
    assert job.job_id == "crashed-job"
    assert job.object_key == "upload/crashed-job.pdf"
    assert job.input_file == "crashed-job.pdf"
    assert job.user_id == 7

    asyncio.run(live.acknowledge_job(redelivered[0]))
    assert redis.pending["conversion_jobs:normal"] == {}


def test_reclaim_stale_jobs_leaves_fresh_pending_jobs_alone() -> None:
    """A message a live consumer is still working on must not be duplicated.

    Idle time is measured from delivery, so the threshold is the guard: at a
    threshold above the conversion timeout an in-flight job is never eligible
    (see ``workers.converter_workers.worker.stale_min_idle_ms``).
    """
    redis = StubRedisStream()
    redis.add("conversion_jobs:low", _fields("in-flight-job"))
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)
    assert asyncio.run(consumer.fetch_job()) is not None

    assert asyncio.run(consumer.reclaim_stale_jobs(min_idle_ms=600_000)) == 0

    # No duplicate was appended and the original is still owned exactly once.
    assert len(redis.entries["conversion_jobs:low"]) == 1
    assert list(redis.pending["conversion_jobs:low"]) == ["1-0"]


def test_reclaim_stale_jobs_acks_the_original_after_requeueing() -> None:
    """The re-queued copy must be written BEFORE the original is ACKed.

    ACKing first would remove the only copy before the replacement exists, so a
    failed XADD would lose the job entirely.
    """
    redis = StubRedisStream()
    redis.add("conversion_jobs:high", _fields("stale-job"))
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)
    asyncio.run(consumer.fetch_job())
    redis.now += 3_600_000

    assert asyncio.run(consumer.reclaim_stale_jobs(min_idle_ms=60_000)) == 1

    # Ordering: the copy was appended, then the original removed.
    assert [(stream, fields["job_id"], maxlen) for stream, fields, maxlen in redis.xadds] == [
        ("conversion_jobs:high", "stale-job", None)
    ]
    assert redis.acked == [("conversion_jobs:high", "conversion-workers", "1-0")]
    assert redis.pending["conversion_jobs:high"] == {}
    # The copy is a fresh entry, so it is delivered by the normal read path.
    assert [message_id for message_id, _ in redis.entries["conversion_jobs:high"]] == ["1-0", "2-0"]


# ----------------------------------------------------------------------
# W-13 — startup logging must make a REDIS_URL mismatch visible
# ----------------------------------------------------------------------


def test_describe_endpoint_never_leaks_credentials() -> None:
    redis = StubRedisStream()
    redis.connection_pool.connection_kwargs = {
        "host": "upstash.example",
        "port": 6379,
        "db": 0,
        "username": "default",
        "password": "super-secret",
    }
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)

    assert consumer.describe_endpoint() == "upstash.example:6379/0"


def test_startup_log_reports_endpoint_streams_and_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = StubRedisStream()
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)
    logger = CapturingLogger()
    monkeypatch.setattr(queue_module, "worker_logger", logger)

    asyncio.run(consumer._ensure_consumer_group_with_retry(max_retries=1))

    assert len(logger.records) == 1
    _, message = logger.records[0]
    assert "redis.test:6379/0" in message
    assert "conversion-workers" in message
    for stream in JobStreamConsumer.STREAMS:
        assert stream in message


# ----------------------------------------------------------------------
# W-19 — unbounded streams
# ----------------------------------------------------------------------


def test_dead_letter_stream_is_bounded() -> None:
    """The dead-letter stream has no consumer, so it needs an explicit cap."""
    redis = StubRedisStream()
    consumer = JobStreamConsumer("conversion-workers", "worker-a", redis)

    asyncio.run(
        consumer.dead_letter_job("conversion_jobs:high:1-0", "boom", _job("dead-job"))
    )

    assert len(redis.xadds) == 1
    stream, fields, maxlen = redis.xadds[0]
    assert stream == "conversion_jobs:dead"
    assert maxlen == 10_000
    assert fields["error"] == "boom"
    assert fields["original_message_id"] == "conversion_jobs:high:1-0"


def test_publish_job_does_not_trim_the_input_streams() -> None:
    """An approximate MAXLEN here could evict an unconsumed job.

    Growth is bounded by (published - consumed), so the loss risk is not worth
    the memory saving; the DLQ and event streams are the unbounded ones.
    """
    redis = StubRedisStream()
    publisher = JobStream(redis)

    asyncio.run(publisher.publish_job(_job("new-job")))

    assert [(stream, maxlen) for stream, _, maxlen in redis.xadds] == [
        ("conversion_jobs", None)
    ]
