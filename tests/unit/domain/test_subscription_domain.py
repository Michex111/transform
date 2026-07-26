import pytest

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.entities.subscription import Subscription
from src.domain.subscriptions.exceptions import (
    GuestTierHasNoPersistentCredits,
    GuestTierRequiresRateLimiting,
    InsufficientCredits,
    MissingCreditAccount,
    StorageQuotaExceeded,
)
from src.domain.subscriptions.policies.tier_policy import GB, MB, TierPolicy
from src.domain.subscriptions.value_object.tier import SubscriptionTier


def test_tier_policy_defaults() -> None:
    guest = TierPolicy.for_tier(SubscriptionTier.GUEST)
    free = TierPolicy.for_tier(SubscriptionTier.FREE)
    premium = TierPolicy.for_tier(SubscriptionTier.PREMIUM)

    assert guest.storage_quota_bytes == 50 * MB
    assert guest.monthly_conversion_credits is None
    assert free.storage_quota_bytes == 5 * GB
    assert free.monthly_conversion_credits == 100
    assert premium.storage_quota_bytes == 50 * GB
    assert premium.monthly_conversion_credits == 5000


def test_credit_is_consumed_for_paid_tiers() -> None:
    subscription = Subscription(tier=SubscriptionTier.FREE)
    credit = Credit.from_tier(owner_id="u-1", period_key="2026-07", tier=SubscriptionTier.FREE)

    subscription.consume_conversion_credit(credit)

    assert credit.remaining == 99


def test_subscription_raises_when_credits_are_missing() -> None:
    subscription = Subscription(tier=SubscriptionTier.PREMIUM)

    with pytest.raises(MissingCreditAccount):
        subscription.consume_conversion_credit(None)


def test_guest_subscription_requires_rate_limiter() -> None:
    subscription = Subscription(tier=SubscriptionTier.GUEST)

    with pytest.raises(GuestTierRequiresRateLimiting):
        subscription.consume_conversion_credit(None)


def test_guest_tier_has_no_persistent_credits() -> None:
    with pytest.raises(GuestTierHasNoPersistentCredits):
        Credit.from_tier(owner_id="guest-ip", period_key="2026-07", tier=SubscriptionTier.GUEST)


def test_credit_consumption_fails_when_empty() -> None:
    credit = Credit(owner_id="u-2", period_key="2026-07", allowance=1, remaining=0)

    with pytest.raises(InsufficientCredits):
        credit.consume_for_conversion(1)


def test_storage_quota_is_enforced() -> None:
    subscription = Subscription(tier=SubscriptionTier.GUEST, used_storage_bytes=40 * MB)

    with pytest.raises(StorageQuotaExceeded):
        subscription.allocate_storage(15 * MB)
