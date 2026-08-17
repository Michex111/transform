from src.application.ports.queue_port import JobQueuePort
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class PriorityQueueDispatcher:
    """
    Publishes conversion jobs to the Redis stream that matches the actor's
    subscription tier, so premium traffic is processed ahead of free/guest.
    """

    def __init__(self, queue_port: JobQueuePort, router: QueuePriorityRouter):
        self._queue_port = queue_port
        self._router = router

    async def dispatch(self, job: ConversionJob, tier: SubscriptionTier) -> None:
        """Enqueue a job on the stream selected by the given tier."""
        stream = self._router.stream_for_tier(tier)
        await self._queue_port.publish_job(job, stream=stream)

