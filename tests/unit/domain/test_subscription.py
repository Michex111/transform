"""Tests for subscription domain entities and policies."""

import pytest
from src.domain.subscriptions.entities.subscription import Subscription
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.exceptions import (
    StorageQuotaExceeded,
    InsufficientCredits,
    GuestTierHasNoPersistentCredits,
    InvalidSubscriptionState,
)


class TestTierPolicy:
    """Tests for tier policy configuration."""

    def test_guest_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.GUEST)
        assert policy.storage_quota_bytes == 50 * 1024 * 1024
        assert policy.monthly_conversion_credits is None
        assert not policy.has_persistent_credits

    def test_free_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.FREE)
        assert policy.storage_quota_bytes == 5 * 1024 * 1024 * 1024
        assert policy.monthly_conversion_credits == 50
        assert policy.has_persistent_credits

    def test_premium_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.PREMIUM)
        assert policy.storage_quota_bytes == 50 * 1024 * 1024 * 1024
        assert policy.monthly_conversion_credits == 500
        assert policy.has_persistent_credits

    def test_pro_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.PRO)
        assert policy.storage_quota_bytes == 50 * 1024 * 1024 * 1024
        assert policy.monthly_conversion_credits == 500
        assert policy.has_persistent_credits

    def test_pro_plus_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.PRO_PLUS)
        assert policy.storage_quota_bytes == 100 * 1024 * 1024 * 1024
        assert policy.monthly_conversion_credits == 2000
        assert policy.has_persistent_credits

    def test_enterprise_policy(self):
        policy = TierPolicy.for_tier(SubscriptionTier.ENTERPRISE)
        assert policy.storage_quota_bytes == 1000 * 1024 * 1024 * 1024
        assert policy.monthly_conversion_credits is None
        assert not policy.has_persistent_credits


class TestSubscription:
    """Tests for Subscription entity."""

    def test_ensure_storage_available_within_quota(self):
        sub = Subscription(tier=SubscriptionTier.FREE)
        # FREE tier has 5GB, 1GB should be fine
        sub.ensure_storage_available(1024 * 1024 * 1024)

    def test_ensure_storage_available_exceeds_quota(self):
        sub = Subscription(tier=SubscriptionTier.FREE)
        with pytest.raises(StorageQuotaExceeded):
            sub.ensure_storage_available(10 * 1024 * 1024 * 1024)  # 10GB

    def test_allocate_storage(self):
        sub = Subscription(tier=SubscriptionTier.FREE)
        sub.allocate_storage(1024 * 1024 * 1024)  # 1GB
        assert sub.used_storage_bytes == 1024 * 1024 * 1024

    def test_release_storage(self):
        sub = Subscription(tier=SubscriptionTier.FREE)
        sub.allocate_storage(1024 * 1024 * 1024)
        sub.release_storage(512 * 1024 * 1024)
        assert sub.used_storage_bytes == 512 * 1024 * 1024

    def test_release_more_than_used(self):
        sub = Subscription(tier=SubscriptionTier.FREE)
        sub.allocate_storage(1024 * 1024)
        with pytest.raises(InvalidSubscriptionState):
            sub.release_storage(2048 * 1024)  # Release more than used


class TestCredit:
    """Tests for Credit entity."""

    def test_credit_from_tier(self):
        credit = Credit.from_tier("owner-1", "2024-01", SubscriptionTier.FREE)
        assert credit.allowance == 50
        assert credit.remaining == 50

    def test_guest_has_no_credits(self):
        with pytest.raises(GuestTierHasNoPersistentCredits):
            Credit.from_tier("owner-1", "2024-01", SubscriptionTier.GUEST)

    def test_consume_credits(self):
        credit = Credit.from_tier("owner-1", "2024-01", SubscriptionTier.FREE)
        credit.consume_for_conversion(5)
        assert credit.remaining == 45

    def test_consume_too_many_credits(self):
        credit = Credit.from_tier("owner-1", "2024-01", SubscriptionTier.FREE)
        with pytest.raises(InsufficientCredits):
            credit.consume_for_conversion(100)

    def test_reset_to_monthly_allowance(self):
        credit = Credit.from_tier("owner-1", "2024-01", SubscriptionTier.FREE)
        credit.consume_for_conversion(30)
        assert credit.remaining == 20
        credit.reset_to_monthly_allowance()
        assert credit.remaining == 50
