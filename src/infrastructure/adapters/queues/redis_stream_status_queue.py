from dataclasses import dataclass
from redis.asyncio import Redis


class JobEventPublisher:
    """
    A class responsible for publishing job events to a Redis stream.
    """
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.stream_name = "conversion_job_events"

    async def publish(self, job_id: str, status: str, progress: int, message: str | None = None) -> None:
        event: dict = {
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "message": message or ""
        }
        await self.redis_client.xadd(self.stream_name, event)
        