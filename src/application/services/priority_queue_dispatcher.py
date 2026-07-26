from src.application.ports.redis_port import RedisQueuePort
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class PriorityQueueDispatcher:
    """Dispatches conversion jobs to tier-specific Redis streams."""

    def __init__(
        self,
        queue_port: RedisQueuePort,
        queue_router: QueuePriorityRouter,
    ) -> None:
        self._queue_port = queue_port
        self._queue_router = queue_router

    async def dispatch(self, job: ConversionJob, tier: SubscriptionTier) -> str:
        """Publishes a conversion job to the stream selected by tier.

        Args:
            job: Conversion job payload.
            tier: Tier determining target stream priority.

        Returns:
            Redis stream message id returned by the adapter.
        """
        stream = self._queue_router.stream_for_tier(tier)
        return await self._queue_port.publish_job(stream=stream, job=job)
