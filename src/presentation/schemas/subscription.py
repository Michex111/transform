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


class CheckoutUiMode(StrEnum):
    """Which checkout surface the client wants the session for.

    ``HOSTED`` keeps Stripe's own page (the historical behaviour and the
    default); ``EMBEDDED`` asks for a session the SPA can mount itself. The
    server may override either via ``STRIPE_CHECKOUT_UI_MODE``.
    """

    HOSTED = "hosted"
    EMBEDDED = "embedded"


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


class AiEntitlementResponse(BaseModel):
    """The AI assistant allowances a plan carries, for the pricing page.

    A deliberate SUBSET of the assistant status payload: this is a **public**
    endpoint, so it exposes the model *level* and its human label but never a
    concrete model id — naming the deployment's provider/model choices to
    anonymous callers is not necessary to sell the plan. Every number here is
    read from the domain policy, so the marketing page cannot advertise an
    allowance the server does not enforce.
    """

    model_level: str
    model_label: str
    requests_per_hour: int
    max_attachments: int
    max_document_mb: int
    max_actions_per_turn: int


class SubscriptionPlanResponse(BaseModel):
    tier: SubscriptionTier
    name: str
    price_monthly_usd: float | None = None
    storage_gb: int
    monthly_credits: int | None = None
    features: list[str]
    ai: AiEntitlementResponse | None = None


class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    # Defaults to hosted so an SPA that has not been updated yet keeps the exact
    # behaviour it was built against.
    ui_mode: CheckoutUiMode = CheckoutUiMode.HOSTED


class CheckoutResponse(BaseModel):
    """How the client should complete the session it just created.

    Exactly one field is populated, chosen by the requested ``ui_mode``: a
    hosted session yields ``checkout_url`` to navigate to, an embedded one
    yields ``client_secret`` to mount Stripe.js with. Both are optional so the
    API and the SPA can be deployed in either order without a window where a
    checkout cannot be started.
    """

    checkout_url: str | None = None
    client_secret: str | None = None


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
