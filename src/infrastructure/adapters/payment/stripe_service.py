"""
Stripe integration service for subscription billing and payments.

Handles checkout sessions, webhook processing, and subscription management.
"""

import logging
from typing import Any

from src.infrastructure.config.settings import get_settings

logger = logging.getLogger(__name__)


class StripeService:
    """
    Wrapper around the Stripe API for subscription management.

    Provides methods for:
    - Creating checkout sessions
    - Managing subscriptions
    - Processing webhook events
    - Handling customer lifecycle
    """

    def __init__(self):
        settings = get_settings()
        self._secret_key = (
            settings.STRIPE_SECRET_KEY.get_secret_value()
            if settings.STRIPE_SECRET_KEY
            else None
        )
        self._webhook_secret = (
            settings.STRIPE_WEBHOOK_SECRET.get_secret_value()
            if settings.STRIPE_WEBHOOK_SECRET
            else None
        )
        self._enabled = bool(self._secret_key)

    @property
    def enabled(self) -> bool:
        """Whether Stripe is configured and enabled."""
        return self._enabled

    async def create_checkout_session(
        self,
        user_id: str,
        email: str,
        tier: str,
        success_url: str,
        cancel_url: str,
    ) -> str | None:
        """
        Create a Stripe checkout session for subscription upgrade.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            tier: The target subscription tier.
            success_url: Redirect URL on successful payment.
            cancel_url: Redirect URL on cancellation.

        Returns:
            The checkout session URL, or None if Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create checkout session")
            return None

        try:
            import stripe
            stripe.api_key = self._secret_key

            # Price IDs come from settings so they can be configured per environment
            settings = get_settings()
            price_ids = {
                "pro": settings.STRIPE_PRICE_PRO,
                "pro_plus": settings.STRIPE_PRICE_PRO_PLUS,
                "enterprise": settings.STRIPE_PRICE_ENTERPRISE,
            }

            price_id = price_ids.get(tier.lower())
            if not price_id:
                raise ValueError(f"Unknown tier: {tier}")

            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price": price_id,
                    "quantity": 1,
                }],
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=email,
                metadata={
                    "user_id": user_id,
                    "tier": tier,
                },
                subscription_data={
                    "metadata": {
                        "user_id": user_id,
                        "tier": tier,
                    }
                },
            )

            logger.info(
                "Created Stripe checkout session",
                extra={"user_id": user_id, "tier": tier, "session_id": session.id},
            )
            return session.url

        except ImportError:
            logger.error("Stripe Python SDK not installed")
            return None
        except Exception as e:
            logger.error("Failed to create Stripe checkout session: %s", e, exc_info=True)
            raise

    async def cancel_subscription(self, subscription_id: str) -> bool:
        """
        Cancel a Stripe subscription at period end.

        Args:
            subscription_id: The Stripe subscription ID.

        Returns:
            True if successful, False otherwise.
        """
        if not self._enabled:
            return False

        try:
            import stripe
            stripe.api_key = self._secret_key

            stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True,
            )
            logger.info("Cancelled subscription at period end", extra={"subscription_id": subscription_id})
            return True

        except Exception as e:
            logger.error("Failed to cancel subscription: %s", e, exc_info=True)
            return False

    async def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        """
        Fetch a Stripe subscription's current state.

        Returns:
            A dict with status, current_period_end (unix ts), and cancel_at_period_end.
        """
        if not self._enabled:
            return None

        try:
            import stripe
            stripe.api_key = self._secret_key

            subscription = stripe.Subscription.retrieve(subscription_id)
            return {
                "status": subscription.status,
                "current_period_end": getattr(subscription, "current_period_end", None),
                "cancel_at_period_end": subscription.cancel_at_period_end,
            }
        except Exception as e:
            logger.error("Failed to fetch subscription: %s", e, exc_info=True)
            return None

    async def create_customer(self, user_id: str, email: str, name: str) -> str | None:
        """
        Create a Stripe customer for a user.

        Args:
            user_id: The user's internal ID.
            email: The user's email.
            name: The user's full name (optional).

        Returns:
            The Stripe customer ID, or None if Stripe is not configured.
        """
        if not self._enabled:
            return None

        try:
            import stripe
            stripe.api_key = self._secret_key

            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"user_id": user_id},
            )
            logger.info("Created Stripe customer", extra={"user_id": user_id, "customer_id": customer.id})
            return customer.id

        except Exception as e:
            logger.error("Failed to create Stripe customer: %s", e, exc_info=True)
            return None

    async def create_payment_intent(
        self,
        amount_usd: float,
        credits: int,
        customer_id: str | None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Create a Stripe payment intent for credit purchases.

        Args:
            amount_usd: The amount to charge in USD.
            credits: The number of credits being purchased.
            customer_id: Optional Stripe customer ID.
            metadata: Additional metadata for the payment.

        Returns:
            Payment intent dict with client_secret, or None.
        """
        if not self._enabled:
            return None

        try:
            import stripe
            stripe.api_key = self._secret_key

            intent_kwargs: dict[str, Any] = {
                "amount": int(amount_usd * 100),  # Convert to cents
                "currency": "usd",
                "metadata": {
                    **(metadata or {}),
                    "credits": str(credits),
                },
            }
            if customer_id:
                intent_kwargs["customer"] = customer_id
            intent = stripe.PaymentIntent.create(**intent_kwargs)
            logger.info("Created payment intent", extra={"intent_id": intent.id, "credits": credits})
            return {"client_secret": intent.client_secret, "id": intent.id}

        except Exception as e:
            logger.error("Failed to create payment intent: %s", e, exc_info=True)
            return None
