"""Subscription management API endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from src.domain.assistant.policies.assistant_policy import (
    hourly_quota,
    max_actions_per_turn,
    max_attachments_for_tier,
    max_document_bytes_for_tier,
    model_label_for_tier,
    model_level_for_tier,
)
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import current_period_key
from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.infrastructure.adapters.payment.stripe_service import SavedPaymentMethod, StripeService
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.config.settings import get_settings
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_credit_repository,
    get_stripe_service,
    get_subscription_repository,
)
from src.presentation.schemas.credit import TransactionType
from src.presentation.schemas.subscription import (
    AiEntitlementResponse,
    CancelSubscriptionResponse,
    ChangePlanRequest,
    ChangePlanResponse,
    CheckoutRequest,
    CheckoutResponse,
    InvoiceListResponse,
    InvoiceResponse,
    PaymentMethodListResponse,
    PaymentMethodSessionResponse,
    PortalResponse,
    ResumeSubscriptionResponse,
    SavedPaymentMethodResponse,
    SubscriptionPlanResponse,
    SubscriptionStatus,
    SubscriptionStatusResponse,
    SubscriptionTier,
    api_tier_to_domain,
    domain_tier_to_api,
)

router = APIRouter(prefix="/api/v1/subscription", tags=["subscription"])

#: Ordering used to decide whether a plan switch is an upgrade (immediate, with
#: proration and carryover) or a downgrade (scheduled at the period end).
#: ``PREMIUM`` is the legacy alias of ``PRO`` and deliberately ranks with it.
_TIER_RANK: dict[DomainTier, int] = {
    DomainTier.GUEST: 0,
    DomainTier.FREE: 0,
    DomainTier.PREMIUM: 1,
    DomainTier.PRO: 1,
    DomainTier.PRO_PLUS: 2,
    DomainTier.ENTERPRISE: 3,
}


def _ai_entitlement(tier: DomainTier) -> AiEntitlementResponse:
    """Build a plan's AI allowance block straight from the domain policy.

    This is the anti-drift bridge: exactly the ``assistant_policy`` functions
    the assistant enforces with are the ones this public page reports, so the
    pricing page and the runtime can never disagree. ``max_document_mb`` is
    converted to whole MiB here (display only) — the policy keeps bytes.
    """
    return AiEntitlementResponse(
        model_level=model_level_for_tier(tier),
        model_label=model_label_for_tier(tier),
        requests_per_hour=hourly_quota(tier),
        max_attachments=max_attachments_for_tier(tier),
        max_document_mb=max_document_bytes_for_tier(tier) // (1024 * 1024),
        max_actions_per_turn=max_actions_per_turn(tier),
    )


# Subscription plans definition
_PLANS = [
    SubscriptionPlanResponse(
        tier=SubscriptionTier.FREE,
        name="Free",
        price_monthly_usd=None,
        storage_gb=5,
        monthly_credits=50,
        features=["5 GB storage", "50 conversions/month", "10 API calls/month", "Community support"],
        ai=_ai_entitlement(DomainTier.FREE),
        # Structured mirrors of the prose above; the two must agree verbatim.
        api_calls_month=10,
        priority_processing=False,
        support_level="Community",
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO,
        name="Pro",
        price_monthly_usd=9.99,
        storage_gb=50,
        monthly_credits=500,
        features=["50 GB storage", "500 conversions/month", "100 API calls/month", "Priority support"],
        ai=_ai_entitlement(DomainTier.PRO),
        api_calls_month=100,
        priority_processing=False,
        support_level="Priority",
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO_PLUS,
        name="Pro Plus",
        price_monthly_usd=24.99,
        storage_gb=100,
        monthly_credits=2000,
        features=["100 GB storage", "2000 conversions/month", "1000 API calls/month", "Priority processing", "24/7 support"],
        ai=_ai_entitlement(DomainTier.PRO_PLUS),
        api_calls_month=1000,
        priority_processing=True,
        support_level="24/7",
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.ENTERPRISE,
        name="Enterprise",
        price_monthly_usd=None,
        storage_gb=1000,
        monthly_credits=None,
        features=["Custom storage", "Unlimited conversions", "Unlimited API access", "Dedicated support", "SLA guarantee", "Custom integrations"],
        ai=_ai_entitlement(DomainTier.ENTERPRISE),
        # ``api_calls_month=None`` matches the prose "Unlimited API access":
        # unlimited has no integer representation, so None means uncapped.
        api_calls_month=None,
        priority_processing=True,
        support_level="Dedicated",
    ),
]


@router.get("/plans", response_model=list[SubscriptionPlanResponse])
async def list_plans() -> list[SubscriptionPlanResponse]:
    """List all available subscription plans."""
    return _PLANS


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    payload: CheckoutRequest,
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> CheckoutResponse:
    """Create a Stripe checkout session for upgrading subscription."""
    if not stripe_service.enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe integration is not configured. Set STRIPE_SECRET_KEY to enable checkout.",
        )
    if payload.tier == SubscriptionTier.FREE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The FREE tier does not require checkout",
        )

    # Reuse an existing Stripe customer if the user already has one, so repeat
    # subscription changes attach to the same customer record.
    row = await subscription_repo.get_subscription_row(current_user.id)
    customer_id = row.stripe_customer_id if row else None

    # Resolve an optional promotion code BEFORE creating anything. A blank or
    # whitespace-only value is treated as absent (no lookup at all); a
    # non-blank code that resolves to nothing is rejected, because silently
    # charging full price when a student typed a code is the worst outcome.
    #
    # The resolved RECORD is passed through, not just the id: the coupon's
    # percentage and duration are only readable from the lookup (a session's
    # `discounts[]` carries neither), and the SPA needs them to explain the
    # discount at all.
    promotion = None
    promotion_code = (payload.promotion_code or "").strip()
    if promotion_code:
        promotion = await stripe_service.resolve_promotion_code(promotion_code)
        if promotion is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That promotion code isn't valid or has expired.",
            )

    settings = get_settings()
    handle = await stripe_service.create_checkout_session(
        user_id=str(current_user.id),
        email=current_user.email,
        tier=payload.tier.value.lower(),
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        customer_id=customer_id,
        ui_mode=payload.ui_mode.value,
        promotion=promotion,
    )
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe checkout session could not be created",
        )
    return CheckoutResponse(
        checkout_url=handle.url,
        client_secret=handle.client_secret,
        amount_total=handle.amount_total,
        currency=handle.currency,
        discount_code=handle.discount_code,
        discount_percent_off=handle.discount_percent_off,
        discount_duration=handle.discount_duration,
    )


@router.post("/change-plan", response_model=ChangePlanResponse)
async def change_plan(
    payload: ChangePlanRequest,
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    credit_repo: Annotated[SQLCreditRepository, Depends(get_credit_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> ChangePlanResponse:
    """Switch an existing paid subscription to another paid tier.

    An upgrade applies immediately with proration: the unspent plan balance is
    moved to expiring carryover and the plan bucket is reset to the new tier's
    grant. A downgrade is scheduled for the end of the current period so the
    customer keeps what they already paid for.

    Deliberately never creates a Checkout Session — that would create a second
    concurrent subscription and bill the customer twice.
    """
    if not stripe_service.enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe integration is not configured. Set STRIPE_SECRET_KEY to enable plan changes.",
        )

    requested = api_tier_to_domain(payload.tier)
    if requested == DomainTier.ENTERPRISE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enterprise is not self-serve; contact sales to change plans.",
        )
    if requested == DomainTier.FREE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the cancel endpoint to move to the FREE tier.",
        )

    row = await subscription_repo.get_subscription_row(current_user.id)
    if row is None or not row.stripe_subscription_id:
        # A FREE user has no subscription to modify; they must start one via
        # checkout. This is the exact case where using checkout is correct.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No active paid subscription to change. Use checkout to start one.",
        )
    if row.tier == requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already subscribed to this plan",
        )

    new_price_id = stripe_service.resolve_price_id(requested.value.lower())
    if not new_price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No self-serve price is configured for this plan",
        )

    is_upgrade = _TIER_RANK.get(requested, 0) > _TIER_RANK.get(row.tier, 0)

    # Capture the OLD plan balance BEFORE changing anything: on an upgrade it
    # becomes carryover, and after the reset below it would be gone.
    period_key = current_period_key()
    old_policy = TierPolicy.for_tier(row.tier)
    old_grant = old_policy.monthly_conversion_credits or 0
    credit = await credit_repo.get_credit(str(current_user.id), period_key)
    old_plan_remaining = credit.remaining if credit is not None else old_grant

    result = await stripe_service.change_subscription_plan(
        row.stripe_subscription_id,
        new_price_id=new_price_id,
        user_id=str(current_user.id),
        tier=requested.value.lower(),
        is_upgrade=is_upgrade,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe subscription could not be changed",
        )

    previous_api_tier = domain_tier_to_api(row.tier)

    if not is_upgrade:
        # Scheduled at period end. The tier (and any carryover) changes when the
        # webhook for the new phase fires, so touch nothing now.
        return ChangePlanResponse(
            tier=domain_tier_to_api(requested),
            previous_tier=previous_api_tier,
            scheduled_effective_at=result.scheduled_effective_at,
            message=(
                "Downgrade scheduled. It takes effect at the end of the current "
                "billing period; you keep your current plan until then."
            ),
        )

    # --- Upgrade: now, on this request ------------------------------------
    # All three writes below are one transaction. They used to commit
    # separately, so a failure between them could leave carryover credited while
    # the plan bucket was never reset (credits created) or the tier switched
    # while the wallet was not (credits lost). The repositories therefore expose
    # ``commit=False`` variants; this block commits once at the end.
    #
    # The Stripe call above has already happened; if any write here fails, the
    # rollback below discards every local change, so the account is left exactly
    # as Stripe still believes it is (the old tier) rather than half-switched.
    new_grant = TierPolicy.for_tier(requested).monthly_conversion_credits or 0
    new_carryover = row.carryover_credits + old_plan_remaining
    try:
        # 1. Carry the unspent plan balance into the expiring carryover pool,
        #    and optimistically record the new tier. Setting the tier here is
        #    what stops a double-clicked upgrade from computing carryover twice
        #    before the webhook lands: the second request then sees the target
        #    tier and 400s.
        row.tier = requested
        await subscription_repo.set_wallet(
            current_user.id,
            carryover_credits=new_carryover,
            carryover_expires_at=result.previous_period_end,
            purchased_credits=row.purchased_credits,
            purchased_credits_first=row.purchased_credits_first,
            commit=False,
        )

        # 2. Reset the plan bucket to the NEW tier's grant. Reset, not top-up:
        #    whatever was left has just become carryover, so adding it here
        #    again would double-count it (Pro 320 left -> Pro Plus 2000 + 320).
        await credit_repo.save_credit(
            Credit(
                owner_id=str(current_user.id),
                period_key=period_key,
                allowance=new_grant,
                remaining=new_grant,
            ),
            commit=False,
        )

        # 3. Ledger row for auditability. No reference_id: a plan change has no
        #    external id to be idempotent against (and repeated upgrades are
        #    already prevented by the tier guard above).
        await credit_repo.record_transaction(
            transaction_id=str(uuid.uuid4()),
            user_id=current_user.id,
            amount=old_plan_remaining,
            transaction_type=TransactionType.CARRYOVER.value,
            description=(
                f"Carried {old_plan_remaining} unspent plan credits over from "
                f"{previous_api_tier.value} to {requested.value}"
            ),
            commit=False,
        )

        await credit_repo.commit()
    except Exception:
        # Nothing above is durable until the commit, but the session may have
        # been flushed; rolling back guarantees no partial wallet survives even
        # if an outer layer were to commit the session.
        await credit_repo.rollback()
        raise

    return ChangePlanResponse(
        tier=domain_tier_to_api(requested),
        previous_tier=previous_api_tier,
        plan_credits=new_grant,
        carryover_credits=new_carryover,
        carryover_expires_at=result.previous_period_end,
        message=(
            "Upgrade applied. Your unspent plan credits were carried over and "
            "expire at the end of your previous billing period."
        ),
    )


@router.post("/portal", response_model=PortalResponse)
async def create_portal_session(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> PortalResponse:
    """Create a Stripe customer portal session for self-service billing."""
    if not stripe_service.enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe integration is not configured.",
        )

    row = await subscription_repo.get_subscription_row(current_user.id)
    customer_id = row.stripe_customer_id if row else None
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Stripe customer found for this account",
        )

    settings = get_settings()
    url = await stripe_service.create_portal_session(
        customer_id=customer_id,
        return_url=settings.STRIPE_PORTAL_RETURN_URL,
    )
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe portal session could not be created",
        )
    return PortalResponse(portal_url=url)


@router.post("/payment-method-session", response_model=PaymentMethodSessionResponse)
async def create_payment_method_session(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> PaymentMethodSessionResponse:
    """Create a Customer Session so the SPA can manage payment methods itself.

    This is the in-app replacement for the Customer Portal's card screen. The
    portal cannot be branded — it is configured in the Stripe Dashboard and
    picks up account-level Branding, with no per-session appearance controls
    (unlike Checkout Sessions, which accept ``branding_settings``). Handing the
    browser a Customer Session lets the Payment Element render the customer's
    saved methods inside our own layout instead.

    Returns ``enabled=False`` rather than raising when Stripe is unconfigured or
    the user has no customer yet, so the Billing page can simply omit the
    section: a Free user has no customer until their first checkout, and that is
    an ordinary state, not an error.
    """
    if not stripe_service.enabled:
        return PaymentMethodSessionResponse(client_secret=None, enabled=False)

    row = await subscription_repo.get_subscription_row(current_user.id)
    customer_id = row.stripe_customer_id if row else None
    if not customer_id:
        return PaymentMethodSessionResponse(client_secret=None, enabled=False)

    client_secret = await stripe_service.create_customer_session(customer_id)
    if client_secret is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment method session could not be created",
        )
    # Best-effort. A server-created SetupIntent is Stripe's documented shape for
    # saving a card without a payment, but the Payment Element can also create
    # its own at confirmation time — so a failure here degrades to that mode
    # rather than blocking card management entirely.
    setup_intent_client_secret = await stripe_service.create_setup_intent(customer_id)
    return PaymentMethodSessionResponse(
        client_secret=client_secret,
        setup_intent_client_secret=setup_intent_client_secret,
        enabled=True,
    )


def _payment_methods_response(
    methods: list[SavedPaymentMethod], *, enabled: bool = True
) -> PaymentMethodListResponse:
    """Map the service's saved-card projections onto the API schema."""
    return PaymentMethodListResponse(
        methods=[
            SavedPaymentMethodResponse(
                id=method.id,
                brand=method.brand,
                last4=method.last4,
                exp_month=method.exp_month,
                exp_year=method.exp_year,
                is_default=method.is_default,
                wallet=method.wallet,
            )
            for method in methods
        ],
        enabled=enabled,
    )


async def _customer_id_for(subscription_repo: SQLSubscriptionRepository, user_id: int) -> str | None:
    row = await subscription_repo.get_subscription_row(user_id)
    return row.stripe_customer_id if row else None


async def _default_payment_methods(
    subscription_repo: SQLSubscriptionRepository,
    stripe_service: StripeService,
    user_id: int,
) -> PaymentMethodListResponse:
    """The caller's cards, with the one a renewal will charge flagged.

    The subscription id is passed through so the service can source
    ``is_default`` from the subscription's own default rather than only the
    customer's: the two can disagree, and a wrong badge here tells the customer
    their next invoice will use a card it will not. Every list-returning
    handler goes through this so all three agree on which card is the default.
    """
    row = await subscription_repo.get_subscription_row(user_id)
    # Written as one guard so the row is narrowed for the attributes read below
    # rather than relying on a truthiness check on a value derived from it.
    if row is None or not row.stripe_customer_id:
        return _payment_methods_response([], enabled=False)
    methods = await stripe_service.list_payment_methods(
        row.stripe_customer_id, subscription_id=row.stripe_subscription_id
    )
    return _payment_methods_response(methods)


@router.get("/payment-methods", response_model=PaymentMethodListResponse)
async def list_payment_methods(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> PaymentMethodListResponse:
    """List the caller's saved cards.

    Mirrors ``/payment-method-session``: unconfigured Stripe and "no customer
    yet" are ordinary states, reported as ``enabled=False`` rather than raised,
    because a Free user has no customer until their first checkout.
    """
    if not stripe_service.enabled:
        return _payment_methods_response([], enabled=False)
    return await _default_payment_methods(subscription_repo, stripe_service, current_user.id)


async def _require_owned_payment_method(
    *,
    current_user_id: int,
    payment_method_id: str,
    subscription_repo: SQLSubscriptionRepository,
    stripe_service: StripeService,
) -> tuple[str, list[SavedPaymentMethod]]:
    """Resolve the caller's Stripe customer and verify the card belongs to it.

    Returns the customer id and the customer's cards. A card that is unknown or
    owned by another customer answers 404 identically, so ids stay unenumerable
    and a caller can never operate on somebody else's payment method.
    """
    if not stripe_service.enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe integration is not configured.",
        )
    customer_id = await _customer_id_for(subscription_repo, current_user_id)
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Stripe customer found for this account",
        )
    methods = await stripe_service.list_payment_methods(customer_id)
    if payment_method_id not in {method.id for method in methods}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found",
        )
    return customer_id, methods


@router.post(
    "/payment-methods/{payment_method_id}/default",
    response_model=PaymentMethodListResponse,
)
async def set_default_payment_method(
    payment_method_id: str,
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> PaymentMethodListResponse:
    """Make one of the caller's saved cards the default for future charges."""
    customer_id, _ = await _require_owned_payment_method(
        current_user_id=current_user.id,
        payment_method_id=payment_method_id,
        subscription_repo=subscription_repo,
        stripe_service=stripe_service,
    )
    row = await subscription_repo.get_subscription_row(current_user.id)
    updated = await stripe_service.set_default_payment_method(
        customer_id,
        payment_method_id,
        # Also pin it on the active subscription: a renewal charges the
        # subscription's own default, which can differ from the customer's.
        subscription_id=row.stripe_subscription_id if row else None,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Default payment method could not be updated",
        )
    return await _default_payment_methods(subscription_repo, stripe_service, current_user.id)


@router.delete(
    "/payment-methods/{payment_method_id}",
    response_model=PaymentMethodListResponse,
)
async def remove_payment_method(
    payment_method_id: str,
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> PaymentMethodListResponse:
    """Detach one of the caller's saved cards.

    Refuses to remove the current default while an active subscription exists.
    Stripe warns that detaching a payment method used by an active subscription
    breaks that subscription, because it removes the customer default a renewal
    falls back to. The user must choose a different default card first.
    """
    _, methods = await _require_owned_payment_method(
        current_user_id=current_user.id,
        payment_method_id=payment_method_id,
        subscription_repo=subscription_repo,
        stripe_service=stripe_service,
    )
    target = next(method for method in methods if method.id == payment_method_id)
    row = await subscription_repo.get_subscription_row(current_user.id)
    if target.is_default and row is not None and row.stripe_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the default payment method for an active subscription. "
                "Set another card as default before removing it."
            ),
        )
    removed = await stripe_service.detach_payment_method(payment_method_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment method could not be removed",
        )
    return await _default_payment_methods(subscription_repo, stripe_service, current_user.id)


@router.get("/status", response_model=SubscriptionStatusResponse)
async def get_subscription_status(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> SubscriptionStatusResponse:
    """Get the current user's subscription status."""
    row = await subscription_repo.get_subscription_row(current_user.id)
    if row is None or row.tier == DomainTier.FREE:
        return SubscriptionStatusResponse(
            tier=SubscriptionTier.FREE,
            status=SubscriptionStatus.ACTIVE,
            cancel_at_period_end=False,
        )

    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end = False
    if row.stripe_subscription_id:
        remote = await stripe_service.get_subscription(row.stripe_subscription_id)
        if remote:
            # Use .get() with a fallback so older Stripe mocks / partial payloads
            # don't crash the status endpoint.
            period_start_ts = remote.get("current_period_start")
            period_end_ts = remote.get("current_period_end")
            if period_start_ts is not None:
                current_period_start = datetime.fromtimestamp(period_start_ts, tz=UTC)
            if period_end_ts is not None:
                current_period_end = datetime.fromtimestamp(period_end_ts, tz=UTC)
            # A scheduled (not yet effective) cancellation: the subscription is
            # still active but will lapse at the period end. Reading it from the
            # remote subscription keeps Stripe the source of truth.
            cancel_at_period_end = bool(remote.get("cancel_at_period_end", False))
            if remote.get("status") == "canceled":
                return SubscriptionStatusResponse(
                    tier=domain_tier_to_api(row.tier),
                    status=SubscriptionStatus.CANCELLED,
                    stripe_subscription_id=row.stripe_subscription_id,
                    cancel_at_period_end=cancel_at_period_end,
                )

    return SubscriptionStatusResponse(
        tier=domain_tier_to_api(row.tier),
        status=SubscriptionStatus.ACTIVE,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        stripe_subscription_id=row.stripe_subscription_id,
        cancel_at_period_end=cancel_at_period_end,
    )


@router.post("/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> CancelSubscriptionResponse:
    """Cancel the current subscription. Downgrades to FREE at period end."""
    row = await subscription_repo.get_subscription_row(current_user.id)
    if row is None or row.tier == DomainTier.FREE or not row.stripe_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active paid subscription to cancel",
        )

    cancelled = await stripe_service.cancel_subscription(row.stripe_subscription_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe subscription could not be cancelled",
        )

    # Keep the tier until the period end; the customer.subscription.deleted
    # webhook performs the downgrade to FREE.
    return CancelSubscriptionResponse(
        message="Subscription will be cancelled at the end of the current billing period.",
        tier_after_cancel="FREE",
    )


@router.post("/resume", response_model=ResumeSubscriptionResponse)
async def resume_subscription(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> ResumeSubscriptionResponse:
    """Undo a scheduled cancellation so the subscription renews again.

    Only clears Stripe's ``cancel_at_period_end`` flag. The local ``tier`` is
    deliberately left alone: the ``customer.subscription.updated`` webhook is
    the single writer for tier, so writing it here could race the webhook and
    leave the two out of step.
    """
    row = await subscription_repo.get_subscription_row(current_user.id)
    if row is None or row.tier == DomainTier.FREE or not row.stripe_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription to resume",
        )

    resumed = await stripe_service.resume_subscription(row.stripe_subscription_id)
    if not resumed:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe subscription could not be resumed",
        )

    return ResumeSubscriptionResponse(
        message="Your subscription will renew again at the end of the current billing period.",
        tier=domain_tier_to_api(row.tier),
        cancel_at_period_end=False,
    )


def _invoice_response(invoice: dict[str, Any]) -> InvoiceResponse:
    """Map one service-side invoice dict onto the API schema.

    The service hands back raw unix seconds; this is the single place they are
    converted to aware UTC datetimes (and to None when the field is absent).
    """
    return InvoiceResponse(
        id=invoice["id"],
        number=invoice.get("number"),
        status=invoice["status"],
        amount_paid=invoice.get("amount_paid") or 0,
        amount_due=invoice.get("amount_due") or 0,
        currency=invoice.get("currency") or "usd",
        created_at=datetime.fromtimestamp(invoice["created"], tz=UTC),
        period_start=(
            datetime.fromtimestamp(invoice["period_start"], tz=UTC)
            if invoice.get("period_start") is not None
            else None
        ),
        period_end=(
            datetime.fromtimestamp(invoice["period_end"], tz=UTC)
            if invoice.get("period_end") is not None
            else None
        ),
        invoice_pdf=invoice.get("invoice_pdf"),
        hosted_invoice_url=invoice.get("hosted_invoice_url"),
    )


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices(
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> InvoiceListResponse:
    """List the caller's most recent invoices for the billing history screen.

    Mirrors ``/payment-methods``: unconfigured Stripe and "no customer yet" are
    ordinary states, reported as ``enabled=False`` rather than raised, because a
    Free user has no customer until their first checkout.
    """
    if not stripe_service.enabled:
        return InvoiceListResponse(enabled=False, invoices=[])

    customer_id = await _customer_id_for(subscription_repo, current_user.id)
    if not customer_id:
        return InvoiceListResponse(enabled=False, invoices=[])

    invoices = await stripe_service.list_invoices(customer_id)
    return InvoiceListResponse(
        enabled=True,
        invoices=[_invoice_response(invoice) for invoice in invoices],
    )

