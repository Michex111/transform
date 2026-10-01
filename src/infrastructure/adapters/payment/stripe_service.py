"""
Stripe integration service for subscription billing and payments.

Handles checkout sessions (subscriptions and credit purchases), customer
portal sessions, webhook processing, and subscription management.

Uses the :class:`stripe.StripeClient` instance API (not the deprecated
global ``stripe.api_key`` pattern) so that API version pinning and per-call
configuration stay on a single client.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from src.infrastructure.config.settings import get_settings

if TYPE_CHECKING:
    # Imported for typing only: `stripe` is imported lazily inside the methods
    # below so that merely constructing this service does not pull in the SDK.
    # A local-variable annotation is never evaluated at runtime, so this stays
    # free.
    from stripe.params import (
        SubscriptionScheduleCreateParams,
        SubscriptionScheduleUpdateParams,
        SubscriptionUpdateParams,
    )
    from stripe.params.checkout import (
        SessionCreateParams,
        SessionCreateParamsBrandingSettings,
    )

logger = logging.getLogger(__name__)


def _stripe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a Stripe object, dict, or plain mock alike.

    The SDK's ``StripeObject`` supports both ``obj[key]`` and attribute access;
    a test double is usually a ``SimpleNamespace`` (attributes only). Reading
    through one helper keeps that difference out of the billing logic.
    """
    if obj is None:
        return default
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return getattr(obj, key, default)
    return default if value is None else value


def _unix_to_datetime(value: Any) -> datetime | None:
    """Convert a Stripe unix timestamp to an aware UTC datetime, or None."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@dataclass(frozen=True, slots=True)
class PlanChangeResult:
    """Outcome of an in-place plan change on an existing subscription.

    ``previous_period_end`` is the subscription's period end captured *before*
    the update. Stripe re-anchors the billing period on an immediate upgrade, so
    reading it afterwards would give the new end and the carryover would outlive
    the period it actually belongs to.
    """

    subscription_id: str
    is_upgrade: bool
    previous_period_end: datetime | None
    #: For a scheduled downgrade, when the new price takes over.
    scheduled_effective_at: datetime | None = None


class CheckoutUiMode(StrEnum):
    """Where the payment form is rendered.

    ``HOSTED`` sends the browser to Stripe's own page (the original behaviour);
    ``EMBEDDED`` renders the same Stripe-hosted form inside our SPA via
    Stripe.js, so the surrounding page is ours.
    """

    HOSTED = "hosted"
    EMBEDDED = "embedded"


@dataclass(frozen=True, slots=True)
class CheckoutSessionHandle:
    """The minimum a browser needs to complete a Checkout Session.

    Exactly one field is populated: a hosted session has a ``url`` to navigate
    to, an embedded one has a ``client_secret`` to mount Stripe.js with.
    ``client_secret`` is designed to be exposed to the browser, but it must
    never be written to a log or an error report.
    """

    url: str | None = None
    client_secret: str | None = None


# Appearance for the embedded form, mirroring the SPA's design tokens in
# ``web/src/index.css``.
#
# `branding_settings` is a **per-session overlay** on the account's Dashboard
# branding, so this is confined to the embedded form: the hosted page, the
# Customer Portal, receipts and emails keep using the account-level settings.
_BRAND_BACKGROUND = "#121417"
_BRAND_PRIMARY = "#5a6bff"
#: Stripe accepts a fixed list of families; `inter` is the SPA's body font.
_BRAND_BODY_FONT = "inter"
#: Stripe offers pill | rectangular | rounded. `rounded` (6px) is the closest
#: match to the SPA's `rounded-lg` controls; `pill` would be a visible lie.
_BRAND_BORDER_STYLE = "rounded"

#: Where the embedded form's logo comes from unless overridden. Served by the
#: SPA, so it follows whichever origin `APP_BASE_URL` points at.
_DERIVED_LOGO_PATH = "apple-touch-icon.png"


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
            if self._secret_key is None:
                raise RuntimeError("Stripe is not configured; cannot build a client.")
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

    def resolve_price_id(self, tier: str) -> str | None:
        """Public form of :meth:`_resolve_price_id` for the plan-change route.

        ``None`` means the tier is not sold self-serve (Enterprise), so a caller
        must not attempt a price switch for it.
        """
        return self._resolve_price_id(tier)

    def _resolve_ui_mode(self, requested: str) -> CheckoutUiMode:
        """Decide how a checkout session is presented.

        ``STRIPE_CHECKOUT_UI_MODE`` wins whenever it names a mode, which is what
        makes the kill switch work without a deploy. ``auto`` defers to the
        caller, so an already-deployed SPA that asks for nothing keeps getting
        the hosted page.

        An unrecognised request degrades to hosted instead of raising: the
        caller is the only thing that would be broken, and the hosted page
        always works.
        """
        configured = get_settings().STRIPE_CHECKOUT_UI_MODE.strip().lower()
        if configured != "auto":
            return CheckoutUiMode(configured)
        try:
            return CheckoutUiMode(requested.strip().lower())
        except ValueError:
            logger.warning(
                "Unknown checkout ui_mode %r; falling back to hosted", requested
            )
            return CheckoutUiMode.HOSTED

    def _branding_settings(self) -> SessionCreateParamsBrandingSettings:
        """The per-session appearance applied to an embedded checkout form.

        Built fresh on every call rather than sharing one module-level dict, so
        adding a logo here can never leak into a later call that should not have
        one.

        The logo is included only when a public https URL is available. Stripe
        fetches it **server-side**, so handing it a localhost or plain-http URL
        fails the session creation itself rather than merely hiding the image.
        """
        branding: SessionCreateParamsBrandingSettings = {
            "background_color": _BRAND_BACKGROUND,
            "button_color": _BRAND_PRIMARY,
            "font_family": _BRAND_BODY_FONT,
            "border_style": _BRAND_BORDER_STYLE,
        }
        logo_url = get_settings().STRIPE_CHECKOUT_LOGO_URL or self._derived_logo_url()
        if logo_url:
            branding["logo"] = {"type": "url", "url": logo_url}
        return branding

    def _derived_logo_url(self) -> str | None:
        """The SPA's touch icon, or None when no public origin is configured."""
        base = get_settings().APP_BASE_URL.strip().rstrip("/")
        if not base.startswith("https://"):
            return None
        return f"{base}/{_DERIVED_LOGO_PATH}"

    def _apply_ui_mode(
        self,
        params: SessionCreateParams,
        ui_mode: CheckoutUiMode,
        *,
        success_url: str,
        cancel_url: str,
    ) -> None:
        """Add the redirect and appearance parameters that depend on the mode.

        The two modes are mutually exclusive at the API level — Stripe rejects
        ``success_url``/``cancel_url`` on an embedded session and requires
        ``return_url`` instead — so keeping the branch in one place means no
        caller can assemble a session Stripe will refuse.

        For an embedded session the success destination **is** the hosted
        session's ``success_url``. Reusing it is what keeps the return redirect,
        and therefore the SPA's existing ``?checkout=success`` handling,
        identical between the two modes.
        """
        if ui_mode is CheckoutUiMode.EMBEDDED:
            params["ui_mode"] = "embedded_page"
            params["return_url"] = success_url
            # `always` lands the customer on the same URL the hosted flow uses,
            # so every existing success affordance in the SPA still fires.
            params["redirect_on_completion"] = "always"
            params["origin_context"] = "web"
            params["branding_settings"] = self._branding_settings()
        else:
            params["success_url"] = success_url
            params["cancel_url"] = cancel_url

    async def create_checkout_session(
        self,
        user_id: str,
        email: str,
        tier: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
        ui_mode: str = CheckoutUiMode.HOSTED,
    ) -> CheckoutSessionHandle | None:
        """
        Create a Stripe checkout session for a subscription upgrade.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            tier: The target subscription tier.
            success_url: Where to land on success. For an embedded session this
                is used as ``return_url`` (Stripe rejects ``success_url``).
            cancel_url: Where to land on cancellation. Hosted mode only.
            customer_id: Optional existing Stripe customer ID.
            ui_mode: ``hosted`` (Stripe's page) or ``embedded`` (our page).

        Returns:
            A handle carrying either the redirect URL or the client secret, or
            None if Stripe is not configured / the tier is not self-serve.
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

        resolved_mode = self._resolve_ui_mode(ui_mode)

        try:
            client = self._get_client()
            params: SessionCreateParams = {
                # NOTE: Omit `payment_method_types` so Stripe dynamically selects
                # eligible payment methods from Dashboard settings.
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "subscription",
                "metadata": {"user_id": user_id, "tier": tier, "kind": "subscription"},
                "subscription_data": {
                    "metadata": {"user_id": user_id, "tier": tier, "kind": "subscription"}
                },
                "integration_identifier": self._integration_identifier(),
            }
            self._apply_ui_mode(
                params,
                resolved_mode,
                success_url=success_url,
                cancel_url=cancel_url,
            )
            # Stripe treats an explicit null and an absent field identically, but
            # the SDK types both fields as `NotRequired[str]`; set only the one
            # that applies instead of passing `None`.
            if customer_id:
                params["customer"] = customer_id
            else:
                params["customer_email"] = email

            session = await asyncio.to_thread(client.v1.checkout.sessions.create, params)

            logger.info(
                "Created Stripe subscription checkout session",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "session_id": session.id,
                    "ui_mode": str(resolved_mode),
                },
            )
            return self._session_handle(session, resolved_mode)

        except Exception as e:
            logger.error("Failed to create Stripe checkout session: %s", e, exc_info=True)
            raise

    @staticmethod
    def _session_handle(
        session: Any, ui_mode: CheckoutUiMode
    ) -> CheckoutSessionHandle:
        """Pick out the one value the browser needs for this mode.

        The two are deliberately exclusive. A hosted session does carry a
        ``client_secret``, but it is useless once the session is hosted, and
        handing the browser a credential it has no use for is exactly the kind
        of thing that ends up in a log line.
        """
        if ui_mode is CheckoutUiMode.EMBEDDED:
            return CheckoutSessionHandle(client_secret=session.client_secret)
        return CheckoutSessionHandle(url=session.url)

    async def create_credit_purchase_session(
        self,
        user_id: str,
        email: str,
        credits: int,
        amount_usd: float,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
        ui_mode: str = CheckoutUiMode.HOSTED,
    ) -> CheckoutSessionHandle | None:
        """
        Create a Stripe checkout session for a one-off credit pack purchase.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            credits: Number of credits being purchased.
            amount_usd: Pre-computed USD price for the credit pack (in dollars).
            success_url: Where to land on success (used as ``return_url`` when
                embedded, since Stripe rejects ``success_url`` there).
            cancel_url: Where to land on cancellation. Hosted mode only.
            customer_id: Optional existing Stripe customer ID.
            ui_mode: ``hosted`` (Stripe's page) or ``embedded`` (our page).

        Returns:
            A handle carrying either the redirect URL or the client secret, or
            None if Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create credit checkout session")
            return None

        unit_amount = int(amount_usd * 100)
        resolved_mode = self._resolve_ui_mode(ui_mode)

        try:
            client = self._get_client()
            params: SessionCreateParams = {
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
                "metadata": {
                    "user_id": user_id,
                    "kind": "credit_purchase",
                    "credits": str(credits),
                },
                "integration_identifier": self._integration_identifier(),
            }
            self._apply_ui_mode(
                params,
                resolved_mode,
                success_url=success_url,
                cancel_url=cancel_url,
            )
            # See the note in `create_checkout_session`: the SDK's TypedDict
            # rejects `None` for these fields, so set only the applicable one.
            if customer_id:
                params["customer"] = customer_id
            else:
                params["customer_email"] = email

            session = await asyncio.to_thread(client.v1.checkout.sessions.create, params)

            logger.info(
                "Created Stripe credit-purchase checkout session",
                extra={
                    "user_id": user_id,
                    "credits": credits,
                    "session_id": session.id,
                    "ui_mode": str(resolved_mode),
                },
            )
            return self._session_handle(session, resolved_mode)

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
            await asyncio.to_thread(
                client.v1.subscriptions.update,
                subscription_id,
                {"cancel_at_period_end": True},
            )
            logger.info("Cancelled subscription at period end", extra={"subscription_id": subscription_id})
            return True

        except Exception as e:
            logger.error("Failed to cancel subscription: %s", e, exc_info=True)
            return False

    async def change_subscription_plan(
        self,
        subscription_id: str,
        *,
        new_price_id: str,
        user_id: str,
        tier: str,
        is_upgrade: bool,
    ) -> PlanChangeResult | None:
        """Switch an EXISTING subscription to a different price.

        This must never go through ``create_checkout_session``: that always
        creates a NEW subscription, so using it for an upgrade would bill the
        customer for a second concurrent subscription on top of the one they
        already have. The subscription id therefore has to come from the caller
        (it is persisted on the user's row).

        Args:
            subscription_id: The Stripe subscription to modify.
            new_price_id: The target tier's Stripe price id.
            user_id: Internal user id, written into the subscription metadata so
                the webhook can resolve the owner.
            tier: Target tier name, written into the metadata so the webhook can
                resolve the tier (``_activate_subscription`` is reachable only
                from webhooks; without this an upgrade would never activate).
            is_upgrade: Immediate proration when True; scheduled at period end
                when False.

        Returns:
            A :class:`PlanChangeResult`, or None when Stripe is not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot change subscription plan")
            return None

        client = self._get_client()
        metadata = {"user_id": user_id, "tier": tier, "kind": "subscription"}

        try:
            # Capture the OLD period end FIRST. An immediate upgrade re-anchors
            # the billing period, so reading it after the update would give the
            # NEW end and the carryover would outlive the period it belongs to.
            subscription = await asyncio.to_thread(
                client.v1.subscriptions.retrieve, subscription_id
            )
            previous_period_end = _unix_to_datetime(
                _stripe_get(subscription, "current_period_end")
            )

            if is_upgrade:
                item_id = self._first_subscription_item_id(subscription)
                params: SubscriptionUpdateParams = {
                    "items": [{"id": item_id, "price": new_price_id}],
                    # Change immediately AND invoice the proration now rather
                    # than folding it into the next renewal, so the customer
                    # sees exactly what the mid-cycle upgrade costs.
                    "proration_behavior": "always_invoice",
                    "metadata": metadata,
                }
                await asyncio.to_thread(
                    client.v1.subscriptions.update, subscription_id, params
                )
                logger.info(
                    "Upgraded Stripe subscription in place",
                    extra={"subscription_id": subscription_id, "tier": tier},
                )
                return PlanChangeResult(
                    subscription_id=subscription_id,
                    is_upgrade=True,
                    previous_period_end=previous_period_end,
                )

            scheduled_effective_at = await self._schedule_price_at_period_end(
                client,
                subscription,
                subscription_id=subscription_id,
                new_price_id=new_price_id,
                metadata=metadata,
            )
            logger.info(
                "Scheduled Stripe subscription downgrade at period end",
                extra={"subscription_id": subscription_id, "tier": tier},
            )
            return PlanChangeResult(
                subscription_id=subscription_id,
                is_upgrade=False,
                previous_period_end=previous_period_end,
                scheduled_effective_at=scheduled_effective_at,
            )

        except Exception as e:
            logger.error("Failed to change Stripe subscription plan: %s", e, exc_info=True)
            raise

    @staticmethod
    def _first_subscription_item_id(subscription: Any) -> str:
        """The id of a subscription's (single) item, which the update targets."""
        items_obj = _stripe_get(subscription, "items")
        items = _stripe_get(items_obj, "data", []) or []
        if not items:
            raise ValueError("Stripe subscription has no items to update")
        item_id = _stripe_get(items[0], "id")
        if not item_id:
            raise ValueError("Stripe subscription item has no id")
        return str(item_id)

    async def _schedule_price_at_period_end(
        self,
        client: Any,
        subscription: Any,
        *,
        subscription_id: str,
        new_price_id: str,
        metadata: dict[str, str],
    ) -> datetime | None:
        """Schedule ``new_price_id`` to take over when the current period ends.

        A subscription price change is otherwise immediate, so deferring one
        requires a **subscription schedule**: the current phase is re-declared
        unchanged and a second phase with the new price is appended to start at
        its end.

        ``proration_behavior="none"`` is the entire point — the current period
        was already paid for at the higher price and must not be re-charged or
        credited. ``end_behavior="release"`` detaches the schedule once the new
        phase begins, leaving an ordinary subscription behind rather than one
        permanently governed by a schedule.

        The new phase carries ``metadata`` because the webhook that fires when
        the phase starts reads ``subscription.metadata.tier`` — without it the
        downgraded tier would never activate.
        """
        schedule_id = _stripe_get(subscription, "schedule")
        if schedule_id:
            schedule = await asyncio.to_thread(
                client.v1.subscription_schedules.retrieve, schedule_id
            )
        else:
            create_params: SubscriptionScheduleCreateParams = {
                "from_subscription": subscription_id,
            }
            schedule = await asyncio.to_thread(
                client.v1.subscription_schedules.create, create_params
            )

        phases_obj = _stripe_get(schedule, "phases")
        phases = _stripe_get(phases_obj, "data", []) or []
        if not phases:
            raise ValueError("Stripe subscription schedule has no phases")
        current_phase = phases[0]

        current_items_obj = _stripe_get(current_phase, "items")
        current_items = _stripe_get(current_items_obj, "data", []) or []
        phase_items: list[Any] = [
            {"price": _stripe_get(item, "price"), "quantity": _stripe_get(item, "quantity", 1)}
            for item in current_items
        ]
        current_start = _stripe_get(current_phase, "start_date")
        current_end = _stripe_get(current_phase, "end_date")

        update_params: SubscriptionScheduleUpdateParams = {
            "end_behavior": "release",
            "proration_behavior": "none",
            "metadata": metadata,
            "phases": [
                {
                    "items": phase_items,
                    "start_date": current_start,
                    "end_date": current_end,
                },
                {
                    "items": [{"price": new_price_id, "quantity": 1}],
                    "metadata": metadata,
                },
            ],
        }
        schedule_id_value = _stripe_get(schedule, "id") or schedule_id
        await asyncio.to_thread(
            client.v1.subscription_schedules.update, schedule_id_value, update_params
        )
        return _unix_to_datetime(current_end)

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
            subscription = await asyncio.to_thread(
                client.v1.subscriptions.retrieve, subscription_id
            )
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
            customer = await asyncio.to_thread(client.v1.customers.create, {
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
            session = await asyncio.to_thread(client.v1.billing_portal.sessions.create, {
                "customer": customer_id,
                "return_url": return_url,
            })
            logger.info("Created Stripe customer portal session", extra={"customer_id": customer_id, "session_id": session.id})
            return session.url

        except Exception as e:
            logger.error("Failed to create portal session: %s", e, exc_info=True)
            return None
