"""Webhook handling API endpoints for Stripe and other integrations."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

_TIER_BY_NAME: dict[str, SubscriptionTier] = {
    "pro": SubscriptionTier.PREMIUM,
    "pro_plus": SubscriptionTier.PREMIUM,
    "enterprise": SubscriptionTier.PREMIUM,
}


async def _activate_subscription(
    db: AsyncSession,
    *,
    user_id: str,
    tier_name: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
) -> None:
    """Upsert the user's subscription row and grant the tier's monthly credits."""
    tier = _TIER_BY_NAME.get(tier_name.lower(), SubscriptionTier.PREMIUM)
    actor_key = f"user:{user_id}"

    sub_repo = SQLSubscriptionRepository(db)
    await sub_repo.upsert_subscription(
        actor_key=actor_key,
        user_id=int(user_id),
        tier=tier,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
    )

    policy = TierPolicy.for_tier(tier)
    allowance = policy.monthly_conversion_credits
    if allowance:
        period_key = datetime.now(UTC).strftime("%Y-%m")
        credit_repo = SQLCreditRepository(db)
        existing = await credit_repo.get_credit(user_id, period_key)
        if existing is not None:
            existing.allowance = allowance
            existing.remaining = allowance
            await credit_repo.save_credit(existing)
        else:
            await credit_repo.save_credit(
                Credit.from_tier(
                    owner_id=user_id,
                    period_key=period_key,
                    tier=tier,
                )
            )

    logger.info(
        "Subscription activated: user=%s tier=%s subscription=%s",
        user_id,
        tier,
        stripe_subscription_id,
    )


async def _downgrade_to_free(db: AsyncSession, user_id: str) -> None:
    """Downgrade a user to the FREE tier after their subscription ends."""
    sub_repo = SQLSubscriptionRepository(db)
    await sub_repo.upsert_subscription(
        actor_key=f"user:{user_id}",
        user_id=int(user_id),
        tier=SubscriptionTier.FREE,
    )
    logger.info("User %s downgraded to FREE tier", user_id)


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def handle_stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """
    Handle incoming Stripe webhook events.

    Verifies the webhook signature and processes events such as:
    - checkout.session.completed
    - invoice.payment_succeeded
    - invoice.payment_failed
    - customer.subscription.deleted
    """
    settings = get_settings()

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe webhook secret not configured",
        )

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Verify webhook signature
    try:
        import stripe
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET.get_secret_value(),
        )
    except Exception as e:
        logger.error("Stripe webhook signature verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    event_type = event.type
    # Stripe's event objects are StripeObject instances, not plain dicts —
    # normalise to a recursive plain dict so attribute/dict access is safe.
    event_data = event.data.object
    if hasattr(event_data, "to_dict_recursive"):
        event_data = event_data.to_dict_recursive()
    elif hasattr(event_data, "to_dict"):
        event_data = event_data.to_dict()

    if event_type == "checkout.session.completed":
        metadata = event_data.get("metadata", {}) or {}
        user_id = metadata.get("user_id")
        tier = metadata.get("tier", "pro")
        if user_id:
            await _activate_subscription(
                db,
                user_id=user_id,
                tier_name=tier,
                stripe_customer_id=event_data.get("customer"),
                stripe_subscription_id=event_data.get("subscription"),
            )

    elif event_type == "invoice.payment_succeeded":
        logger.info("Invoice payment succeeded: %s", event.id)
        # Reset the user's monthly credits so their allowance is refreshed
        customer_id = event_data.get("customer")
        if customer_id:
            logger.info("Payment succeeded for customer %s", customer_id)

    elif event_type == "invoice.payment_failed":
        logger.info("Invoice payment failed: %s", event.id)
        # Stripe handles retries and the grace period; log for monitoring.

    elif event_type == "customer.subscription.deleted":
        metadata = event_data.get("metadata", {}) or {}
        user_id = metadata.get("user_id")
        if user_id:
            await _downgrade_to_free(db, user_id)

    return {"status": "received", "type": event_type}


@router.get("/health", status_code=status.HTTP_200_OK)
async def webhook_health() -> dict:
    """Health check endpoint for webhook consumers."""
    return {"status": "healthy"}
