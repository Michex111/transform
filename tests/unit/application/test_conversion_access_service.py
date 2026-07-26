import asyncio
from datetime import UTC, datetime

import pytest

from src.application.dtos.subscription_dto import ConversionActor
from src.application.exceptions.subscription_exceptions import (
    GuestRateLimitExceeded,
    MissingActorIdentity,
)
from src.application.services.conversion_access_service import ConversionAccessService
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.exceptions import InsufficientCredits
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class InMemorySubscriptionRepository:
    def __init__(self) -> None:
        self.usage_by_actor: dict[str, int] = {}

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        del actor_key
        return SubscriptionTier.FREE

    async def get_used_storage_bytes(self, actor_key: str) -> int:
        return self.usage_by_actor.get(actor_key, 0)

    async def set_used_storage_bytes(self, actor_key: str, used_storage_bytes: int) -> None:
        self.usage_by_actor[actor_key] = used_storage_bytes


class InMemoryCreditRepository:
    def __init__(self) -> None:
        self.by_owner_period: dict[tuple[str, str], Credit] = {}

    async def get_credit(self, owner_id: str, period_key: str) -> Credit | None:
        return self.by_owner_period.get((owner_id, period_key))

    async def save_credit(self, credit: Credit) -> None:
        self.by_owner_period[(credit.owner_id, credit.period_key)] = credit


class StubRateLimiter:
    def __init__(self, allow_result: bool) -> None:
        self.allow_result = allow_result
        self.requests: list[tuple[str, int, int]] = []

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        self.requests.append((key, limit, window_seconds))
        return self.allow_result


def test_authorize_conversion_consumes_credit_for_free_tier() -> None:
    subscription_repo = InMemorySubscriptionRepository()
    credit_repo = InMemoryCreditRepository()
    rate_limiter = StubRateLimiter(allow_result=True)
    service = ConversionAccessService(
        subscription_repository=subscription_repo,
        credit_repository=credit_repo,
        rate_limiter=rate_limiter,
        queue_router=QueuePriorityRouter(),
        now_provider=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    actor = ConversionActor(actor_key="user:1", user_id="1", tier=SubscriptionTier.FREE)

    result = asyncio.run(service.authorize_conversion(actor, incoming_file_size_bytes=1024))

    assert result.queue_stream == "conversion_jobs:normal"
    assert result.credits_remaining == 99
    saved_credit = asyncio.run(credit_repo.get_credit("1", "2026-07"))
    assert saved_credit is not None
    assert saved_credit.remaining == 99
    assert rate_limiter.requests == []


def test_authorize_conversion_requires_guest_rate_limit() -> None:
    service = ConversionAccessService(
        subscription_repository=InMemorySubscriptionRepository(),
        credit_repository=InMemoryCreditRepository(),
        rate_limiter=StubRateLimiter(allow_result=False),
        queue_router=QueuePriorityRouter(),
    )
    actor = ConversionActor(
        actor_key="guest:10.0.0.1",
        ip_address="10.0.0.1",
        tier=SubscriptionTier.GUEST,
    )

    with pytest.raises(GuestRateLimitExceeded):
        asyncio.run(service.authorize_conversion(actor, incoming_file_size_bytes=1024))


def test_authorize_conversion_rejects_missing_user_on_persistent_tier() -> None:
    service = ConversionAccessService(
        subscription_repository=InMemorySubscriptionRepository(),
        credit_repository=InMemoryCreditRepository(),
        rate_limiter=StubRateLimiter(allow_result=True),
        queue_router=QueuePriorityRouter(),
    )
    actor = ConversionActor(actor_key="user:missing", tier=SubscriptionTier.PREMIUM)

    with pytest.raises(MissingActorIdentity):
        asyncio.run(service.authorize_conversion(actor, incoming_file_size_bytes=1024))


def test_authorize_conversion_rejects_when_credits_are_empty() -> None:
    subscription_repo = InMemorySubscriptionRepository()
    credit_repo = InMemoryCreditRepository()
    asyncio.run(
        credit_repo.save_credit(
            Credit(owner_id="2", period_key="2026-07", allowance=100, remaining=0)
        )
    )
    service = ConversionAccessService(
        subscription_repository=subscription_repo,
        credit_repository=credit_repo,
        rate_limiter=StubRateLimiter(allow_result=True),
        queue_router=QueuePriorityRouter(),
        now_provider=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    actor = ConversionActor(actor_key="user:2", user_id="2", tier=SubscriptionTier.FREE)

    with pytest.raises(InsufficientCredits):
        asyncio.run(service.authorize_conversion(actor, incoming_file_size_bytes=1024))
