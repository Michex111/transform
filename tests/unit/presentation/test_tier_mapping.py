"""Tests for the API ↔ domain subscription tier mapping."""

from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.presentation.schemas.subscription import (
    SubscriptionTier,
    api_tier_to_domain,
    domain_tier_to_api,
)


def test_api_free_maps_to_domain_free() -> None:
    assert api_tier_to_domain(SubscriptionTier.FREE) is DomainTier.FREE


def test_api_paid_tiers_map_to_premium() -> None:
    assert api_tier_to_domain(SubscriptionTier.PRO) is DomainTier.PREMIUM
    assert api_tier_to_domain(SubscriptionTier.PRO_PLUS) is DomainTier.PREMIUM
    assert api_tier_to_domain(SubscriptionTier.ENTERPRISE) is DomainTier.PREMIUM


def test_domain_guest_and_free_map_to_api_free() -> None:
    assert domain_tier_to_api(DomainTier.GUEST) is SubscriptionTier.FREE
    assert domain_tier_to_api(DomainTier.FREE) is SubscriptionTier.FREE


def test_domain_premium_maps_to_api_pro() -> None:
    assert domain_tier_to_api(DomainTier.PREMIUM) is SubscriptionTier.PRO


def test_round_trip_through_api_tier() -> None:
    for api_tier in SubscriptionTier:
        assert domain_tier_to_api(api_tier_to_domain(api_tier)) is not None
