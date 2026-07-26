from redis.asyncio import Redis

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.infrastructure.adapters.queues.messages import ConversionJobMessage


class RedisPriorityQueueAdapter:
    """Redis stream producer that supports per-tier stream routing."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis_client = redis_client

    async def publish_job(self, stream: str, job: ConversionJob) -> str:
        """Publishes conversion job payload to the selected stream."""
        payload = ConversionJobMessage.from_conversion_job(job).to_dict()
        message_id = await self._redis_client.xadd(stream, payload)
        return str(message_id)
