class FakeEventPublisher:
    def __init__(self) -> None:
        self.published_events: list[dict[str, object]] = []

    async def publish(
        self,
        job_id: str,
        status: str,
        progress: int,
        message: str | None = None,
    ) -> None:
        self.published_events.append(
            {
                "job_id": job_id,
                "status": str(status),
                "progress": progress,
                "message": message,
            }
        )