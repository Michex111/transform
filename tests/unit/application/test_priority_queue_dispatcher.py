"""Tests for the priority queue dispatcher and tier routing."""

import asyncio

import pytest

from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from tests.fakes.fake_queue import FakeQueuePort


@pytest.fixture
def job() -> ConversionJob:
    return ConversionJob(
        job_id="job-1",
        conversion=ConversionType("pdf", "docx"),
        input_file="input.pdf",
    )


@pytest.mark.parametrize(
    "tier,expected_stream",
    [
        (SubscriptionTier.GUEST, "conversion_jobs:low"),
        (SubscriptionTier.FREE, "conversion_jobs:normal"),
        (SubscriptionTier.PREMIUM, "conversion_jobs:high"),
    ],
)
def test_dispatcher_routes_to_tier_stream(tier, expected_stream, job) -> None:
    queue = FakeQueuePort()
    dispatcher = PriorityQueueDispatcher(queue_port=queue, router=QueuePriorityRouter())

    asyncio.run(dispatcher.dispatch(job, tier=tier))

    assert len(queue.pushed_jobs) == 1
    assert queue.pushed_jobs[0] is job
    assert queue.pushed_streams == [expected_stream]


def test_router_maps_all_tiers() -> None:
    router = QueuePriorityRouter()
    assert router.stream_for_tier(SubscriptionTier.GUEST) == "conversion_jobs:low"
    assert router.stream_for_tier(SubscriptionTier.FREE) == "conversion_jobs:normal"
    assert router.stream_for_tier(SubscriptionTier.PREMIUM) == "conversion_jobs:high"
