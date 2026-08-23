"""Tests for the API ↔ domain subscription tier mapping."""

from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.presentation.schemas.subscription import (
    SubscriptionTier,
    api_tier_to_domain,
    domain_tier_to_api,
)


def test_api_free_maps_to_domain_free() -> None:
    assert api_tier_to_domain(SubscriptionTier.FREE) is DomainTier.FREE


def test_api_paid_tiers_map_to_distinct_domain() -> None:
    assert api_tier_to_domain(SubscriptionTier.PRO) is DomainTier.PRO
    assert api_tier_to_domain(SubscriptionTier.PRO_PLUS) is DomainTier.PRO_PLUS
    assert api_tier_to_domain(SubscriptionTier.ENTERPRISE) is DomainTier.ENTERPRISE


def test_domain_guest_and_free_map_to_api_free() -> None:
    assert domain_tier_to_api(DomainTier.GUEST) is SubscriptionTier.FREE
    assert domain_tier_to_api(DomainTier.FREE) is SubscriptionTier.FREE


def test_domain_premium_maps_to_api_pro() -> None:
    assert domain_tier_to_api(DomainTier.PREMIUM) is SubscriptionTier.PRO


def test_domain_paid_tiers_map_to_distinct_api() -> None:
    assert domain_tier_to_api(DomainTier.PRO) is SubscriptionTier.PRO
    assert domain_tier_to_api(DomainTier.PRO_PLUS) is SubscriptionTier.PRO_PLUS
    assert domain_tier_to_api(DomainTier.ENTERPRISE) is SubscriptionTier.ENTERPRISE


def test_round_trip_through_api_tier() -> None:
    for api_tier in SubscriptionTier:
        assert domain_tier_to_api(api_tier_to_domain(api_tier)) is not None
