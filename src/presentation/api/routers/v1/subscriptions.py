"""Subscription management API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.config.settings import get_settings
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_stripe_service,
    get_subscription_repository,
)
from src.presentation.schemas.subscription import (
    CancelSubscriptionResponse,
    CheckoutRequest,
    CheckoutResponse,
    SubscriptionPlanResponse,
    SubscriptionStatus,
    SubscriptionStatusResponse,
    SubscriptionTier,
    domain_tier_to_api,
)

router = APIRouter(prefix="/api/v1/subscription", tags=["subscription"])

# Subscription plans definition
_PLANS = [
    SubscriptionPlanResponse(
        tier=SubscriptionTier.FREE,
        name="Free",
        price_monthly_usd=None,
        storage_gb=5,
        monthly_credits=50,
        features=["5 GB storage", "50 conversions/month", "10 API calls/month", "Community support"],
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO,
        name="Pro",
        price_monthly_usd=9.99,
        storage_gb=50,
        monthly_credits=500,
        features=["50 GB storage", "500 conversions/month", "100 API calls/month", "Priority support"],
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.PRO_PLUS,
        name="Pro Plus",
        price_monthly_usd=24.99,
        storage_gb=100,
        monthly_credits=2000,
        features=["100 GB storage", "2000 conversions/month", "1000 API calls/month", "Priority processing", "24/7 support"],
    ),
    SubscriptionPlanResponse(
        tier=SubscriptionTier.ENTERPRISE,
        name="Enterprise",
        price_monthly_usd=None,
        storage_gb=1000,
        monthly_credits=None,
        features=["Custom storage", "Unlimited conversions", "Unlimited API access", "Dedicated support", "SLA guarantee", "Custom integrations"],
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

    settings = get_settings()
    url = await stripe_service.create_checkout_session(
        user_id=str(current_user.id),
        email=current_user.email,
        tier=payload.tier.value.lower(),
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
    )
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe checkout session could not be created",
        )
    return CheckoutResponse(checkout_url=url)


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
            current_period_end = datetime.fromtimestamp(
                remote["current_period_end"], tz=UTC
            )
            if remote["status"] == "canceled":
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

