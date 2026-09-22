"""Tests for per-environment Redis stream namespacing.

Development and production share one Redis, so the stream names must be
qualified per environment or their workers steal each other's jobs. These tests
pin the properties that keep that safe:

* an empty prefix is a **no-op** (production's names are unchanged);
* a set prefix reaches *every* stream the queue touches — reads, consumer-group
  creation, ACKs, dead letters and job events. A prefix applied to reads but not
  ACKs (or vice versa) would split a consumer from its own messages, which is
  worse than no prefix at all;
* qualification is idempotent, so a name cannot end up double-prefixed.
"""

import asyncio
import types

import pytest

from src.infrastructure.adapters.queues import (
    redis_stream_job_queue as jsq,
    redis_stream_status_queue as ssq,
    stream_names,
)
from src.infrastructure.adapters.queues.stream_names import (
    JOB_DEAD_LETTER_STREAM,
    JOB_EVENT_STREAM,
    JOB_STREAM,
    JOB_TIER_STREAMS,
    qualify,
    qualify_all,
    stream_prefix,
)


class _FakeRedis:
    """Records the stream names the adapter passes to Redis."""

    def __init__(self):
        self.xgroup_created: list[str] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.xadd_calls: list[tuple[str, dict]] = []

    async def xgroup_create(self, stream, group, id=None, mkstream=False):
        self.xgroup_created.append(stream)

    async def xack(self, stream, group, message_id):
        self.xack_calls.append((stream, group, message_id))
        return 1

    async def xadd(self, stream, fields, maxlen=None):
        self.xadd_calls.append((stream, dict(fields)))
        return "1-0"


@pytest.fixture
def prefix(monkeypatch):
    """Set QUEUE_STREAM_PREFIX for the duration of a test."""

    def _apply(value: str):
        monkeypatch.setattr(
            stream_names, "get_settings", lambda: types.SimpleNamespace(QUEUE_STREAM_PREFIX=value)
        )

    return _apply


# --------------------------------------------------------------------------- #
# the helper itself
# --------------------------------------------------------------------------- #


def test_empty_prefix_is_a_noop(prefix):
    """Production leaves the setting unset, so names must be untouched."""
    prefix("")
    assert stream_prefix() == ""
    assert qualify(JOB_STREAM) == "conversion_jobs"
    assert qualify_all(JOB_TIER_STREAMS) == JOB_TIER_STREAMS
    assert qualify_all(JOB_TIER_STREAMS) == (
        "conversion_jobs:high",
        "conversion_jobs:normal",
        "conversion_jobs:low",
        "conversion_jobs",
    )


def test_prefix_is_applied(prefix):
    prefix("dev:")
    assert stream_prefix() == "dev:"
    assert qualify(JOB_STREAM) == "dev:conversion_jobs"
    assert qualify_all(JOB_TIER_STREAMS) == (
        "dev:conversion_jobs:high",
        "dev:conversion_jobs:normal",
        "dev:conversion_jobs:low",
        "dev:conversion_jobs",
    )
    assert qualify(JOB_EVENT_STREAM) == "dev:conversion_job_events"
    assert qualify(JOB_DEAD_LETTER_STREAM) == "dev:conversion_jobs:dead"


def test_prefixed_names_never_collide_with_production(prefix):
    """The whole point: dev and prod must not share a single key."""
    prefix("")
    production = set(qualify_all(JOB_TIER_STREAMS)) | {
        qualify(JOB_EVENT_STREAM),
        qualify(JOB_DEAD_LETTER_STREAM),
    }
    prefix("dev:")
    development = set(qualify_all(JOB_TIER_STREAMS)) | {
        qualify(JOB_EVENT_STREAM),
        qualify(JOB_DEAD_LETTER_STREAM),
    }
    assert not (production & development)


def test_qualify_is_idempotent(prefix):
    """A name that has already been qualified must not be prefixed twice."""
    prefix("dev:")
    once = qualify(JOB_STREAM)
    assert qualify(once) == once == "dev:conversion_jobs"


# --------------------------------------------------------------------------- #
# job queue: reads, group creation, ACKs, dead letters
# --------------------------------------------------------------------------- #


def test_consumer_reads_the_qualified_streams(prefix):
    prefix("dev:")
    consumer = jsq.JobStreamConsumer("conversion-workers", "worker-a", _FakeRedis())

    assert consumer.streams == (
        "dev:conversion_jobs:high",
        "dev:conversion_jobs:normal",
        "dev:conversion_jobs:low",
        "dev:conversion_jobs",
    )
    assert consumer.stream_name == "dev:conversion_jobs"
    # The base tuple stays the canonical (production) names.
    assert jsq.JobStreamConsumer.STREAMS == JOB_TIER_STREAMS


def test_consumer_group_is_created_on_qualified_streams(prefix):
    prefix("dev:")
    redis = _FakeRedis()
    consumer = jsq.JobStreamConsumer("conversion-workers", "worker-a", redis)

    asyncio.run(consumer._ensure_consumer_group())

    assert redis.xgroup_created == list(consumer.streams)
    assert all(name.startswith("dev:") for name in redis.xgroup_created)


def test_ack_routing_understands_qualified_delivery_keys(prefix):
    """`fetch_job` returns `"<stream>:<id>"`; a prefixed stream has 3 colons.

    If ACK routing matched against the unqualified names it would push the
    ACK to the wrong stream (Redis returns 0 and raises nothing), leaving the
    message pending forever.
    """
    prefix("dev:")
    redis = _FakeRedis()
    consumer = jsq.JobStreamConsumer("conversion-workers", "worker-a", redis)

    asyncio.run(consumer.acknowledge_job("dev:conversion_jobs:high:1700000000000-0"))

    assert redis.xack_calls == [
        ("dev:conversion_jobs:high", "conversion-workers", "1700000000000-0")
    ]


def test_dead_letter_uses_the_qualified_stream(prefix, monkeypatch):
    prefix("dev:")
    redis = _FakeRedis()
    consumer = jsq.JobStreamConsumer("conversion-workers", "worker-a", redis)

    # Stub the message serialisation; only the target stream name is under test.
    monkeypatch.setattr(
        jsq,
        "JobMessage",
        types.SimpleNamespace(
            from_conversion_job=lambda job: types.SimpleNamespace(to_dict=lambda: {"job_id": "j1"})
        ),
    )

    asyncio.run(consumer.dead_letter_job("dev:conversion_jobs:high:1-0", "boom", object()))

    assert redis.xadd_calls == [
        (
            "dev:conversion_jobs:dead",
            {
                "job_id": "j1",
                "error": "boom",
                "original_message_id": "dev:conversion_jobs:high:1-0",
            },
        )
    ]


def test_tier_router_targets_the_qualified_streams(prefix):
    from src.application.services.queue_priority_router import QueuePriorityRouter
    from src.domain.subscriptions.value_object.tier import SubscriptionTier

    prefix("dev:")
    router = QueuePriorityRouter()

    assert router.stream_for_tier(SubscriptionTier.GUEST) == "dev:conversion_jobs:low"
    assert router.stream_for_tier(SubscriptionTier.FREE) == "dev:conversion_jobs:normal"
    assert router.stream_for_tier(SubscriptionTier.PRO) == "dev:conversion_jobs:high"


def test_tier_router_defaults_to_production_names(prefix):
    """With no prefix configured the router must still match the publish streams."""
    from src.application.services.queue_priority_router import QueuePriorityRouter
    from src.domain.subscriptions.value_object.tier import SubscriptionTier

    prefix("")
    router = QueuePriorityRouter()

    assert router.stream_for_tier(SubscriptionTier.GUEST) == "conversion_jobs:low"
    assert router.stream_for_tier(SubscriptionTier.FREE) == "conversion_jobs:normal"
    assert router.stream_for_tier(SubscriptionTier.PRO) == "conversion_jobs:high"
    # Producer and consumer must agree on the names, or jobs land in a stream
    # nobody ever reads.
    produced = {router.stream_for_tier(t) for t in SubscriptionTier}
    assert produced <= set(JOB_TIER_STREAMS)
    assert produced <= set(jsq.JobStreamConsumer("g", "n", _FakeRedis()).streams)


def test_tier_router_targets_the_qualified_streams_consumer_reads(prefix):
    """The prefixed producer/consumer names must still line up exactly."""
    from src.application.services.queue_priority_router import QueuePriorityRouter
    from src.domain.subscriptions.value_object.tier import SubscriptionTier

    prefix("dev:")
    router = QueuePriorityRouter()
    consumer = jsq.JobStreamConsumer("conversion-workers", "worker-a", _FakeRedis())

    produced = {router.stream_for_tier(t) for t in SubscriptionTier}
    assert produced <= set(consumer.streams)
    assert "conversion_jobs:high" not in produced  # not the production names


# --------------------------------------------------------------------------- #
# job events (SSE)
# --------------------------------------------------------------------------- #


def test_event_publisher_uses_the_qualified_stream(prefix):
    prefix("dev:")
    publisher = ssq.JobEventPublisher(_FakeRedis())

    assert publisher.stream_name == "dev:conversion_job_events"


def test_event_subscriber_uses_the_qualified_stream(prefix):
    prefix("dev:")
    subscriber = ssq.JobEventSubscriber(_FakeRedis())

    assert subscriber.stream_name == "dev:conversion_job_events"


def test_event_subscriber_honours_an_explicit_stream_name(prefix):
    """An explicit name (tests, replay tooling) must win over the default."""
    prefix("dev:")
    subscriber = ssq.JobEventSubscriber(_FakeRedis(), stream_name="custom:events")

    assert subscriber.stream_name == "custom:events"
