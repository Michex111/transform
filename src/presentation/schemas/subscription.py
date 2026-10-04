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
    default); ``EMBEDDED`` asks for a session the SPA mounts with Stripe's
    embedded-Checkout entry point; ``ELEMENTS`` asks for one the SPA mounts
    with the Payment Element instead.

    ``ELEMENTS`` exists because embedded Checkout **cannot be themed dark**:
    its ``branding_settings`` covers only background, button, font and shape,
    Stripe rejects a ``theme``/``color_mode`` parameter outright (verified
    against the live account), and its payment sheet renders white regardless
    of ``background_color``. The Payment Element is themed through the
    Appearance API, which does support a dark theme — and the Checkout Session
    behind it is unchanged, so webhooks and fulfilment behave identically.
    """

    HOSTED = "hosted"
    EMBEDDED = "embedded"
    ELEMENTS = "elements"


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
    # Structured mirrors of the prose ``features`` strings above. All optional
    # with a None default so a previously-deployed SPA (which only read the
    # prose) keeps working, and so the API and SPA can be rolled out in either
    # order without a window where a plan is unrenderable.
    api_calls_month: int | None = None
    priority_processing: bool | None = None
    support_level: str | None = None


class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    # Defaults to hosted so an SPA that has not been updated yet keeps the exact
    # behaviour it was built against.
    ui_mode: CheckoutUiMode = CheckoutUiMode.HOSTED
    # Customer-facing promotion code ("a free month of Pro"). Optional and
    # blank-tolerant: an SPA that never sends it — or sends an empty string —
    # gets the exact pre-promo behaviour. Resolution to a Stripe ``promo_…`` id
    # happens server-side and an unknown/expired code is a 400, never a silent
    # full-price charge.
    promotion_code: str | None = None


class CheckoutResponse(BaseModel):
    """How the client should complete the session it just created.

    Exactly one of ``checkout_url``/``client_secret`` is populated, chosen by
    the requested ``ui_mode``: a hosted session yields ``checkout_url`` to
    navigate to, an embedded one yields ``client_secret`` to mount Stripe.js
    with. Both are optional so the API and the SPA can be deployed in either
    order without a window where a checkout cannot be started.

    The discount fields are a read-back of what Stripe actually applied, so the
    SPA can show "100% off" and ``$0.00`` from the server's word rather than
    re-deriving it. Every one is optional/defaulted so an older SPA that ignores
    them is unaffected.
    """

    checkout_url: str | None = None
    client_secret: str | None = None
    #: Session total in minor units (e.g. cents), after discounts, from Stripe.
    amount_total: int | None = None
    #: ISO currency, lowercase (e.g. ``"usd"``).
    currency: str | None = None
    #: The customer-facing code actually applied, when Stripe expanded it.
    discount_code: str | None = None
    #: Percent taken off by the applied coupon (100.0 for a free-month promo).
    discount_percent_off: float | None = None
    #: ``"once"`` | ``"repeating"`` | ``"forever"``.
    discount_duration: str | None = None


class PortalResponse(BaseModel):
    portal_url: str


class PaymentMethodSessionResponse(BaseModel):
    """What the SPA needs to render the card form for this customer.

    Two secrets, and the browser needs both: ``client_secret`` is the Customer
    Session (which saved methods to display, and consent to save a new one) and
    ``setup_intent_client_secret`` is the SetupIntent the new card is attached
    to. ``enabled = False`` when Stripe is unconfigured or the account has no
    customer yet, so the SPA hides the section instead of mounting an element
    that cannot work.
    """

    client_secret: str | None = None
    #: The SetupIntent's client secret. Optional per the independent-deploy
    #: rule: an older API omits it, and the SPA then mounts the Payment Element
    #: in its deferred mode (which creates the intent at confirmation time)
    #: instead of failing.
    setup_intent_client_secret: str | None = None
    enabled: bool = True


class SavedPaymentMethodResponse(BaseModel):
    """One saved card, for the in-app billing screen.

    Only what the card list renders plus the flag it acts on. ``is_default`` is
    computed by the server from the subscription's own default (falling back to
    the customer's invoice settings), so the client never has to guess which
    card a renewal will charge.
    """

    id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int
    is_default: bool = False
    #: `"apple_pay"` / `"google_pay"` / `"link"` when the card was tokenised
    #: through a wallet, else None. Optional, so a client that predates the
    #: field is unaffected.
    wallet: str | None = None


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
    # Defaults to False so older clients that never read it — and every state
    # where no cancellation is scheduled — see the pre-existing meaning: the
    # subscription renews.
    cancel_at_period_end: bool = False


class CancelSubscriptionResponse(BaseModel):
    message: str
    tier_after_cancel: str = "FREE"


class ResumeSubscriptionResponse(BaseModel):
    """Acknowledges an undone cancellation.

    ``cancel_at_period_end`` is always False here: resuming means exactly that
    the subscription renews again. ``tier`` is deliberately *unchanged* — no
    local tier write happens on resume, because the
    ``customer.subscription.updated`` webhook remains the single writer.
    """

    message: str
    tier: SubscriptionTier
    cancel_at_period_end: bool = False


class InvoiceResponse(BaseModel):
    """One billing-history row, mapped from a Stripe Invoice.

    ``created_at``/``period_start``/``period_end`` are already-tz-aware UTC
    datetimes by the time they reach here: the service returns raw unix ints
    and the router performs the single conversion, so the same timestamp has
    exactly one representation in the API.
    """

    id: str
    number: str | None = None
    status: str
    amount_paid: int = 0
    amount_due: int = 0
    currency: str = "usd"
    created_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    invoice_pdf: str | None = None
    hosted_invoice_url: str | None = None


class InvoiceListResponse(BaseModel):
    """The caller's invoices, or a signal to hide the section.

    ``enabled = False`` when Stripe is unconfigured or the user has no customer
    yet — both ordinary states (a Free user has no customer until checkout) —
    mirroring ``/payment-methods``. Invoices are a read-only convenience and
    are never a reason to fail the request.
    """

    enabled: bool = True
    invoices: list[InvoiceResponse] = Field(default_factory=list)


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
