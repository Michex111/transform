from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.subscriptions.value_object.tier import SubscriptionTier


def test_stream_for_tier_uses_expected_priorities() -> None:
    router = QueuePriorityRouter()

    assert router.stream_for_tier(SubscriptionTier.GUEST) == "conversion_jobs:low"
    assert router.stream_for_tier(SubscriptionTier.FREE) == "conversion_jobs:normal"
    assert router.stream_for_tier(SubscriptionTier.PREMIUM) == "conversion_jobs:high"
