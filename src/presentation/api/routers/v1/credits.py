"""Credit and billing API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    next_period_start,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier
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
from src.presentation.schemas.credit import (
    CreditBalanceResponse,
    CreditPricingResponse,
    CreditPurchaseRequest,
    CreditTransactionResponse,
    TransactionType,
)
from src.presentation.schemas.subscription import CheckoutResponse, domain_tier_to_api

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])

# Credit bundle pricing (credits → price USD)
_PRICING = [
    CreditPricingResponse(credits=100, price_usd=10.0, price_per_credit=0.10),
    CreditPricingResponse(credits=500, price_usd=40.0, price_per_credit=0.08),
    CreditPricingResponse(credits=1000, price_usd=70.0, price_per_credit=0.07),
    CreditPricingResponse(credits=5000, price_usd=300.0, price_per_credit=0.06),
]

_FALLBACK_PRICE_PER_CREDIT = 0.10


def _price_for_amount(amount: int) -> float:
    """Return the USD price for a credit bundle (largest bundle ≤ amount, else fallback)."""
    exact = next((p for p in _PRICING if p.credits == amount), None)
    if exact is not None:
        return exact.price_usd
    largest = max((p for p in _PRICING if p.credits < amount), key=lambda p: p.credits, default=None)
    if largest is not None:
        return round(amount * largest.price_per_credit, 2)
    return round(amount * _FALLBACK_PRICE_PER_CREDIT, 2)


def _to_transaction(row) -> CreditTransactionResponse:
    return CreditTransactionResponse(
        id=row.id,
        amount=row.amount,
        transaction_type=TransactionType(row.transaction_type),
        reference_id=row.reference_id,
        description=row.description,
        created_at=row.created_at,
    )


@router.get("/balance", response_model=CreditBalanceResponse)
async def get_credit_balance(
    current_user: CurrentUser,
    credit_repo: Annotated[SQLCreditRepository, Depends(get_credit_repository)],
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> CreditBalanceResponse:
    """Get the current user's credit balance for the active monthly period."""
    tier = await subscription_repo.get_tier_for_user(current_user.id)
    policy = TierPolicy.for_tier(tier)
    monthly_credits = policy.monthly_conversion_credits
    allowance = monthly_credits or 0

    # One instant per request so the period key and the reset date can never
    # straddle a month boundary.
    now = datetime.now(UTC)
    period_key = current_period_key(now)
    credit = await credit_repo.get_credit(str(current_user.id), period_key)

    remaining = credit.remaining if credit is not None else allowance

    return CreditBalanceResponse(
        balance=remaining,
        tier=domain_tier_to_api(tier).value,
        monthly_allowance=allowance,
        monthly_remaining=remaining,
        credits_reset_at=next_period_start(now) if monthly_credits is not None else None,
    )


@router.get("/history", response_model=list[CreditTransactionResponse])
async def get_credit_history(
    current_user: CurrentUser,
    credit_repo: Annotated[SQLCreditRepository, Depends(get_credit_repository)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> list[CreditTransactionResponse]:
    """Get the current user's credit transaction history."""
    offset = (page - 1) * page_size
    rows = await credit_repo.list_transactions(current_user.id, offset=offset, limit=page_size)
    return [_to_transaction(r) for r in rows]


@router.post("/purchase", response_model=CheckoutResponse)
async def purchase_credits(
    payload: CreditPurchaseRequest,
    current_user: CurrentUser,
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    stripe_service: Annotated[StripeService, Depends(get_stripe_service)],
) -> CheckoutResponse:
    """Create a Stripe Checkout session for a one-off credit pack purchase.

    Credits are granted only after Stripe confirms payment (via the
    ``checkout.session.completed`` webhook handler) so users cannot be credited
    for payments that are never captured.
    """
    if not stripe_service.enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe integration is not configured. Set STRIPE_SECRET_KEY to enable purchases.",
        )

    # Look up the user's stripe customer (if one already exists) so repeat
    # purchases attach to the same customer record.
    row = await subscription_repo.get_subscription_row(current_user.id)
    customer_id = row.stripe_customer_id if row else None
    email = current_user.email

    amount_usd = _price_for_amount(payload.amount)
    settings = get_settings()
    url = await stripe_service.create_credit_purchase_session(
        user_id=str(current_user.id),
        email=email,
        credits=payload.amount,
        amount_usd=amount_usd,
        success_url=settings.STRIPE_CREDIT_SUCCESS_URL,
        cancel_url=settings.STRIPE_CREDIT_CANCEL_URL,
        customer_id=customer_id,
    )
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe checkout session could not be created",
        )
    return CheckoutResponse(checkout_url=url)


@router.get("/pricing", response_model=list[CreditPricingResponse])
async def get_credit_pricing() -> list[CreditPricingResponse]:
    """Get credit pricing information."""
    return _PRICING
