import asyncio

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.queues.redis_priority_queue_adapter import RedisPriorityQueueAdapter
from src.infrastructure.adapters.queues.redis_rate_limiter_adapter import RedisRateLimiterAdapter


class _FakePipeline:
    def __init__(self, parent: "_FakeRedisClient") -> None:
        self._parent = parent
        self._ops: list[tuple[str, tuple]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type
        del exc
        del tb

    def incr(self, key: str) -> None:
        self._ops.append(("incr", (key,)))

    def expire(self, key: str, ttl: int, nx: bool = False) -> None:
        self._ops.append(("expire", (key, ttl, nx)))

    async def execute(self) -> list[int]:
        results: list[int] = []
        for op, args in self._ops:
            if op == "incr":
                key = args[0]
                self._parent.counters[key] = self._parent.counters.get(key, 0) + 1
                results.append(self._parent.counters[key])
            elif op == "expire":
                key, ttl, nx = args
                if nx and key in self._parent.expirations:
                    results.append(0)
                else:
                    self._parent.expirations[key] = ttl
                    results.append(1)
        return results


class _FakeRedisClient:
    def __init__(self) -> None:
        self.stream_payloads: list[tuple[str, dict]] = []
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def xadd(self, stream: str, payload: dict) -> str:
        self.stream_payloads.append((stream, payload))
        return "1-0"

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        del transaction
        return _FakePipeline(self)


def test_redis_priority_queue_adapter_publishes_to_selected_stream() -> None:
    async def run() -> None:
        redis = _FakeRedisClient()
        adapter = RedisPriorityQueueAdapter(redis)
        job = ConversionJob(
            job_id="job-1",
            conversion=ConversionType("docx", "pdf"),
            input_file="uploads/a.docx",
        )

        message_id = await adapter.publish_job("conversion_jobs:high", job)

        assert message_id == "1-0"
        assert redis.stream_payloads[0][0] == "conversion_jobs:high"
        assert redis.stream_payloads[0][1]["job_id"] == "job-1"

    asyncio.run(run())


def test_redis_rate_limiter_adapter_blocks_after_limit() -> None:
    async def run() -> None:
        redis = _FakeRedisClient()
        adapter = RedisRateLimiterAdapter(redis)

        assert await adapter.allow("guest:127.0.0.1", limit=2, window_seconds=60) is True
        assert await adapter.allow("guest:127.0.0.1", limit=2, window_seconds=60) is True
        assert await adapter.allow("guest:127.0.0.1", limit=2, window_seconds=60) is False

    asyncio.run(run())
