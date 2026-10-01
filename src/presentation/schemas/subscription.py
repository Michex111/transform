"""Subscription-related API schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

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


class PaymentMethodSessionResponse(BaseModel):
    """A Stripe Customer Session for managing payment methods in our own UI.

    Exactly one field is populated: a ``client_secret`` when Stripe is
    configured, or ``enabled = False`` when it is not, so the SPA can hide the
    card-management section instead of rendering an empty Payment Element.
    """

    client_secret: str | None = None
    enabled: bool = True


class SavedPaymentMethodResponse(BaseModel):
    """One saved card, for the in-app billing screen.

    Only what the card list renders plus the flag it acts on. ``is_default`` is
    computed by the server from the customer's invoice settings, so the client
    never has to guess which card a renewal will charge.
    """

    id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int
    is_default: bool = False


class PaymentMethodListResponse(BaseModel):
    """The caller's saved cards.

    ``enabled = False`` when Stripe is unconfigured or the user has no customer
    yet — both ordinary states (a Free user has no customer until checkout) —
    mirroring ``/payment-method-session`` so the SPA can hide the section rather
    than show an empty Payment Element.
    """

    methods: list[SavedPaymentMethodResponse] = Field(default_factory=list)
    enabled: bool = True


class SubscriptionStatusResponse(BaseModel):
    tier: SubscriptionTier
    status: SubscriptionStatus
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    stripe_subscription_id: str | None = None


class CancelSubscriptionResponse(BaseModel):
    message: str
    tier_after_cancel: str = "FREE"


class ChangePlanRequest(BaseModel):
    """Move an EXISTING paid subscription to another paid tier.

    Distinct from ``CheckoutRequest``: checkout creates a *new* subscription, so
    using it for an upgrade would double-bill a customer who already has one.
    """

    tier: SubscriptionTier


class ChangePlanResponse(BaseModel):
    """What a plan change did, for the UI to confirm.

    ``tier`` is the target tier. For an immediate upgrade it is already active;
    for a scheduled downgrade ``scheduled_effective_at`` says when it takes
    over and the account stays on ``previous_tier`` until then.
    """

    tier: SubscriptionTier
    previous_tier: SubscriptionTier
    #: The new plan bucket's grant after the change.
    plan_credits: int = 0
    #: Total carryover held after the change (the unspent plan balance moved
    #: there on an upgrade; unchanged on a downgrade).
    carryover_credits: int = 0
    carryover_expires_at: datetime | None = None
    #: When a scheduled downgrade becomes effective; None for an upgrade.
    scheduled_effective_at: datetime | None = None
    message: str
