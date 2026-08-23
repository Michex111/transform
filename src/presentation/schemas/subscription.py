"""Subscription-related API schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier


class SubscriptionTier(StrEnum):
    FREE = "FREE"
    PRO = "PRO"
    PRO_PLUS = "PRO_PLUS"
    ENTERPRISE = "ENTERPRISE"


class SubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    TRIAL = "TRIAL"


def api_tier_to_domain(tier: SubscriptionTier) -> DomainTier:
    """Map the API-facing tier enum to the domain/persistence enum."""
    return {
        SubscriptionTier.FREE: DomainTier.FREE,
        SubscriptionTier.PRO: DomainTier.PRO,
        SubscriptionTier.PRO_PLUS: DomainTier.PRO_PLUS,
        SubscriptionTier.ENTERPRISE: DomainTier.ENTERPRISE,
    }[tier]


def domain_tier_to_api(tier: DomainTier) -> SubscriptionTier:
    """Map the domain/persistence tier enum to the API-facing enum."""
    return {
        DomainTier.GUEST: SubscriptionTier.FREE,
        DomainTier.FREE: SubscriptionTier.FREE,
        DomainTier.PREMIUM: SubscriptionTier.PRO,
        DomainTier.PRO: SubscriptionTier.PRO,
        DomainTier.PRO_PLUS: SubscriptionTier.PRO_PLUS,
        DomainTier.ENTERPRISE: SubscriptionTier.ENTERPRISE,
    }[tier]


def domain_tier_name(tier: DomainTier) -> str:
    """Return the lowercase Stripe plan name for a domain tier."""
    return {
        DomainTier.PREMIUM: "pro",
        DomainTier.PRO: "pro",
        DomainTier.PRO_PLUS: "pro_plus",
        DomainTier.ENTERPRISE: "enterprise",
    }.get(tier, "pro")


class SubscriptionPlanResponse(BaseModel):
    tier: SubscriptionTier
    name: str
    price_monthly_usd: float | None = None
    storage_gb: int
    monthly_credits: int | None = None
    features: list[str]


class CheckoutRequest(BaseModel):
    tier: SubscriptionTier


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class SubscriptionStatusResponse(BaseModel):
    tier: SubscriptionTier
    status: SubscriptionStatus
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    stripe_subscription_id: str | None = None


class CancelSubscriptionResponse(BaseModel):
    message: str
    tier_after_cancel: str = "FREE"
