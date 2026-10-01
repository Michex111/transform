"""Subscription management API endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

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
from src.infrastructure.adapters.payment.stripe_service import StripeService
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
    PortalResponse,
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
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO,
        name="Pro",
        price_monthly_usd=9.99,
        storage_gb=50,
        monthly_credits=500,
        features=["50 GB storage", "500 conversions/month", "100 API calls/month", "Priority support"],
        ai=_ai_entitlement(DomainTier.PRO),
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO_PLUS,
        name="Pro Plus",
        price_monthly_usd=24.99,
        storage_gb=100,
        monthly_credits=2000,
        features=["100 GB storage", "2000 conversions/month", "1000 API calls/month", "Priority processing", "24/7 support"],
        ai=_ai_entitlement(DomainTier.PRO_PLUS),
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.ENTERPRISE,
        name="Enterprise",
        price_monthly_usd=None,
        storage_gb=1000,
        monthly_credits=None,
        features=["Custom storage", "Unlimited conversions", "Unlimited API access", "Dedicated support", "SLA guarantee", "Custom integrations"],
        ai=_ai_entitlement(DomainTier.ENTERPRISE),
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

    settings = get_settings()
    handle = await stripe_service.create_checkout_session(
        user_id=str(current_user.id),
        email=current_user.email,
        tier=payload.tier.value.lower(),
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
        customer_id=customer_id,
        ui_mode=payload.ui_mode.value,
    )
    if handle is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe checkout session could not be created",
        )
    return CheckoutResponse(checkout_url=handle.url, client_secret=handle.client_secret)


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
    # 1. Carry the unspent plan balance into the expiring carryover pool, and
    #    optimistically record the new tier. Setting the tier here is what stops
    #    a double-clicked upgrade from computing carryover twice before the
    #    webhook lands: the second request then sees the target tier and 400s.
    new_carryover = row.carryover_credits + old_plan_remaining
    row.tier = requested
    await subscription_repo.set_wallet(
        current_user.id,
        carryover_credits=new_carryover,
        carryover_expires_at=result.previous_period_end,
        purchased_credits=row.purchased_credits,
        purchased_credits_first=row.purchased_credits_first,
    )

    # 2. Reset the plan bucket to the NEW tier's grant. Reset, not top-up:
    #    whatever was left has just become carryover, so adding it here again
    #    would double-count it (Pro 320 left -> Pro Plus must be 2000 + 320).
    new_grant = TierPolicy.for_tier(requested).monthly_conversion_credits or 0
    await credit_repo.save_credit(
        Credit(
            owner_id=str(current_user.id),
            period_key=period_key,
            allowance=new_grant,
            remaining=new_grant,
        )
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
    )

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
        )

    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
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
            if remote.get("status") == "canceled":
                return SubscriptionStatusResponse(
                    tier=domain_tier_to_api(row.tier),
                    status=SubscriptionStatus.CANCELLED,
                    stripe_subscription_id=row.stripe_subscription_id,
                )

    return SubscriptionStatusResponse(
        tier=domain_tier_to_api(row.tier),
        status=SubscriptionStatus.ACTIVE,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        stripe_subscription_id=row.stripe_subscription_id,
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

