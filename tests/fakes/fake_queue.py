from src.domain.conversions.entities.conversion_job import ConversionJob


class FakeQueuePort:
    def __init__(self) -> None:
        self.pending: list[tuple[str, ConversionJob]] = []
        self.pushed_jobs: list[ConversionJob] = []
        self.pushed_streams: list[str | None] = []
        self.acked_messages: list[str] = []
        self.failed_messages: list[tuple[str, str]] = []
        self.dead_lettered: list[tuple[str, str, ConversionJob]] = []
        # Chronological log across every operation, so tests can assert on the
        # ORDER of calls (e.g. dead-letter before ACK) which the per-operation
        # lists above cannot show.
        self.events: list[tuple[str, str]] = []
        self._sequence = 0

    async def publish_job(self, job: ConversionJob, stream: str | None = None) -> None:
        self._sequence += 1
        self.pending.append((f"message-{self._sequence}", job))
        self.pushed_jobs.append(job)
        self.pushed_streams.append(stream)

    async def fetch_job(self) -> tuple[str, ConversionJob] | None:
        if not self.pending:
            return None
        message_id, job = self.pending.pop(0)
        self.events.append(("fetch", message_id))
        return message_id, job

    async def acknowledge_job(self, message_id: str) -> None:
        self.events.append(("ack", message_id))
        self.acked_messages.append(message_id)

    async def fail_job(self, message_id: str, error_message: str) -> None:
        self.events.append(("fail", message_id))
        self.failed_messages.append((message_id, error_message))

    async def dead_letter_job(self, message_id: str, error_message: str, job: ConversionJob) -> None:
        self.events.append(("dead_letter", message_id))
        self.dead_lettered.append((message_id, error_message, job))

    async def reclaim_stale_jobs(self, min_idle_ms: int = 60_000, count: int = 20) -> int:
        # This fake does NOT model a pending-entries list, so it cannot know that
        # a message was abandoned by a crashed consumer and always reports 0.
        # The reclaim behaviour is covered against the real ``JobStreamConsumer``
        # and a Redis stub (tests/unit/infrastructure/test_job_stream_consumer.py).
        del min_idle_ms, count
        return 0

    def preload(self, job: ConversionJob, message_id: str = "message-1") -> None:
        self.pending.append((message_id, job))