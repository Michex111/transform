"""
Stripe integration service for subscription billing and payments.

Handles checkout sessions (subscriptions and credit purchases), customer
portal sessions, webhook processing, and subscription management.

Uses the :class:`stripe.StripeClient` instance API (not the deprecated
global ``stripe.api_key`` pattern) so that API version pinning and per-call
configuration stay on a single client.
"""

import logging
from typing import Any

from src.infrastructure.config.settings import get_settings

logger = logging.getLogger(__name__)


class StripeService:
    """
    Wrapper around the Stripe API for subscription and credit billing.

    Provides methods for:
    - Creating subscription checkout sessions
    - Creating credit-pack checkout sessions
    - Creating customer portal sessions
    - Managing subscriptions
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
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        """Whether Stripe is configured and enabled."""
        return self._enabled

    def _get_client(self):
        """Lazily build the StripeClient instance using the secret key."""
        if self._client is None:
            import stripe
            self._client = stripe.StripeClient(self._secret_key)
        return self._client

    @staticmethod
    def _integration_identifier() -> str:
        """Return an ``integration_identifier`` label for checkout sessions.

        Stripe recommends tagging sessions with a custom label (plus an 8-char
        random suffix) so they can be tracked and compared in the Dashboard.
        """
        import secrets
        return f"transform_{secrets.token_hex(4)}"

    def _resolve_price_id(self, tier: str) -> str | None:
        """Map a tier name to its configured Stripe price ID."""
        settings = get_settings()
        price_ids = {
            "pro": settings.STRIPE_PRICE_PRO,
            "pro_plus": settings.STRIPE_PRICE_PRO_PLUS,
            "enterprise": settings.STRIPE_PRICE_ENTERPRISE,
        }
        return price_ids.get(tier.lower())

    async def create_checkout_session(
        self,
        user_id: str,
        email: str,
        tier: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
    ) -> str | None:
        """
        Create a Stripe checkout session for a subscription upgrade.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            tier: The target subscription tier.
            success_url: Redirect URL on successful payment.
            cancel_url: Redirect URL on cancellation.
            customer_id: Optional existing Stripe customer ID.

        Returns:
            The checkout session URL, or None if Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create checkout session")
            return None

        price_id = self._resolve_price_id(tier)
        if not price_id:
            # Enterprise and any un-provisioned tier are sold via contact
            # sales; there is no self-serve checkout price.
            logger.info("No Stripe price configured for tier '%s'; skipping checkout", tier)
            return None

        try:
            client = self._get_client()
            session = client.v1.checkout.sessions.create({
                # NOTE: Omit `payment_method_types` so Stripe dynamically selects
                # eligible payment methods from Dashboard settings.
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "subscription",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "customer_email": email if not customer_id else None,
                "customer": customer_id,
                "metadata": {"user_id": user_id, "tier": tier, "kind": "subscription"},
                "subscription_data": {
                    "metadata": {"user_id": user_id, "tier": tier, "kind": "subscription"}
                },
                "integration_identifier": self._integration_identifier(),
            })

            logger.info(
                "Created Stripe subscription checkout session",
                extra={"user_id": user_id, "tier": tier, "session_id": session.id},
            )
            return session.url

        except Exception as e:
            logger.error("Failed to create Stripe checkout session: %s", e, exc_info=True)
            raise

    async def create_credit_purchase_session(
        self,
        user_id: str,
        email: str,
        credits: int,
        amount_usd: float,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
    ) -> str | None:
        """
        Create a Stripe checkout session for a one-off credit pack purchase.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            credits: Number of credits being purchased.
            amount_usd: Pre-computed USD price for the credit pack (in dollars).
            success_url: Redirect URL on successful payment.
            cancel_url: Redirect URL on cancellation.
            customer_id: Optional existing Stripe customer ID.

        Returns:
            The checkout session URL, or None if Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create credit checkout session")
            return None

        unit_amount = int(amount_usd * 100)

        try:
            client = self._get_client()
            session = client.v1.checkout.sessions.create({
                # Omit `payment_method_types` for dynamic payment methods.
                "line_items": [{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": f"{credits} Conversion Credits"},
                        "unit_amount": unit_amount,
                    },
                    "quantity": 1,
                }],
                "mode": "payment",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "customer_email": email if not customer_id else None,
                "customer": customer_id,
                "metadata": {
                    "user_id": user_id,
                    "kind": "credit_purchase",
                    "credits": str(credits),
                },
                "integration_identifier": self._integration_identifier(),
            })

            logger.info(
                "Created Stripe credit-purchase checkout session",
                extra={"user_id": user_id, "credits": credits, "session_id": session.id},
            )
            return session.url

        except Exception as e:
            logger.error("Failed to create credit checkout session: %s", e, exc_info=True)
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
            client = self._get_client()
            client.v1.subscriptions.update(subscription_id, {"cancel_at_period_end": True})
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
            client = self._get_client()
            subscription = client.v1.subscriptions.retrieve(subscription_id)
            return {
                "status": subscription.status,
                "current_period_start": getattr(subscription, "current_period_start", None),
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
            client = self._get_client()
            customer = client.v1.customers.create({
                "email": email,
                "name": name,
                "metadata": {"user_id": user_id},
            })
            logger.info("Created Stripe customer", extra={"user_id": user_id, "customer_id": customer.id})
            return customer.id

        except Exception as e:
            logger.error("Failed to create Stripe customer: %s", e, exc_info=True)
            return None

    async def create_portal_session(self, customer_id: str, return_url: str) -> str | None:
        """
        Create a Stripe customer portal session (self-service billing).

        Args:
            customer_id: The Stripe customer ID.
            return_url: Where to redirect the user after leaving the portal.

        Returns:
            The portal URL, or None if Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create portal session")
            return None

        try:
            client = self._get_client()
            session = client.v1.billing_portal.sessions.create({
                "customer": customer_id,
                "return_url": return_url,
            })
            logger.info("Created Stripe customer portal session", extra={"customer_id": customer_id, "session_id": session.id})
            return session.url

        except Exception as e:
            logger.error("Failed to create portal session: %s", e, exc_info=True)
            return None
