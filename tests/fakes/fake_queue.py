from src.domain.entities.conversion_job import ConversionJob


class FakeQueuePort:
    def __init__(self) -> None:
        self.pending: list[tuple[str, ConversionJob]] = []
        self.pushed_jobs: list[ConversionJob] = []
        self.acked_messages: list[str] = []
        self.failed_messages: list[tuple[str, str]] = []
        self._sequence = 0

    async def push_job(self, job: ConversionJob) -> None:
        self._sequence += 1
        self.pending.append((f"message-{self._sequence}", job))
        self.pushed_jobs.append(job)

    async def fetch_job(self) -> tuple[str, ConversionJob] | None:
        if not self.pending:
            return None
        return self.pending.pop(0)

    async def acknowledge_job(self, message_id: str) -> None:
        self.acked_messages.append(message_id)

    async def fail_job(self, message_id: str, error_message: str) -> None:
        self.failed_messages.append((message_id, error_message))

    def preload(self, job: ConversionJob, message_id: str = "message-1") -> None:
        self.pending.append((message_id, job))