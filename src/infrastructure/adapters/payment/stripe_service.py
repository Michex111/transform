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


def _stripe_list(obj: Any) -> list[Any]:
    """The entries of a Stripe "list" field, whichever shape it arrives in.

    The SDK is NOT uniform here, and the difference is invisible to a
    hand-written test double: ``subscription.items`` is a ``ListObject``
    (a ``{data: [...]}`` envelope), while a subscription *schedule*'s
    ``phases`` and a phase's ``items`` are plain Python lists.

    Reading ``.data`` off a plain list yields nothing at all — which is
    precisely how the downgrade path raised "schedule has no phases" against
    real Stripe while the unit test, whose double returned an envelope for
    *every* container, stayed green.
    """
    if obj is None:
        return []
    if isinstance(obj, list):
        return list(obj)
    data = _stripe_get(obj, "data")
    return list(data) if isinstance(data, list) else []


def _price_id(price: Any) -> str | None:
    """The **id** of a price, whether Stripe sent the id or the expanded object.

    ``SubscriptionScheduleUpdateParamsPhaseItem.price`` is declared ``str``
    ("The ID of the price object"), but the same field legitimately arrives
    expanded as a whole ``Price`` on ``subscription.items[].price``. Handing the
    object back to a request would make the SDK serialize every read-only field
    of it and Stripe would reject the call, so callers must pass the id through
    here first. Returns ``None`` when there is no id to send.

    The same id-or-expanded-object problem as ``_payment_method_id`` below, for
    the other field that Stripe expands in some payloads and not others.
    """
    if isinstance(price, str):
        return price or None
    price_id = _stripe_get(price, "id")
    return str(price_id) if price_id else None


@dataclass(frozen=True, slots=True)
class SavedPaymentMethod:
    """One saved card, in the shape the SPA's billing page needs.

    Deliberately a narrow projection of the Stripe object: only the fields the
    card list renders and the single flag it acts on. ``is_default`` is derived
    from the **subscription's** ``default_payment_method`` when there is one
    (falling back to the customer's ``invoice_settings``), because that is the
    value a renewal actually charges — reading only the customer side made the
    "Default" badge point at a card the next invoice would not use.
    """

    id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int
    is_default: bool
    #: `"apple_pay"` / `"google_pay"` / `"link"` when the card was tokenised
    #: through a wallet, else None. Appended last so positional construction in
    #: existing callers stays valid.
    wallet: str | None = None


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
    ``EMBEDDED`` renders Stripe's embedded Checkout page inside our SPA via
    Stripe.js; ``ELEMENTS`` renders the **Payment Element** inside our SPA
    against the same Checkout Session.

    ``ELEMENTS`` is the one that can carry our dark theme. Embedded Checkout
    exposes only background/button/font/shape through ``branding_settings`` —
    it rejects a ``theme`` parameter (verified live) and paints its payment
    sheet white whatever ``background_color`` says — whereas the Payment
    Element is themed with the Appearance API, exactly like the Billing page's
    card form already is. The underlying Checkout Session, and therefore every
    webhook and fulfilment path, is identical.
    """

    HOSTED = "hosted"
    EMBEDDED = "embedded"
    ELEMENTS = "elements"


@dataclass(frozen=True, slots=True)
class CheckoutSessionHandle:
    """The minimum a browser needs to complete a Checkout Session.

    Exactly one of ``url``/``client_secret`` is populated: a hosted session has
    a ``url`` to navigate to, an embedded one has a ``client_secret`` to mount
    Stripe.js with. ``client_secret`` is designed to be exposed to the browser,
    but it must never be written to a log or an error report.

    The remaining fields are a defensive read-back of what Stripe actually
    applied to the session (total, currency, discount), so the API can report
    the applied promotion instead of guessing. They are all optional/defaulted
    so every existing construction — including the credit-purchase path and
    hand-written test doubles that predate them — stays valid.
    """

    url: str | None = None
    client_secret: str | None = None
    #: Session total in minor units (e.g. cents), after discounts.
    amount_total: int | None = None
    #: ISO currency, lowercase.
    currency: str | None = None
    #: The customer-facing promotion code that was applied, when expandable.
    discount_code: str | None = None
    #: Percent off from the applied coupon.
    discount_percent_off: float | None = None
    #: Coupon duration: ``"once"``, ``"repeating"`` or ``"forever"``.
    discount_duration: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPromotion:
    """A customer-facing promotion code, resolved against Stripe.

    This is the **source of truth** for the response's discount fields, and that
    is not a preference — it is the only place the facts exist. A resolved
    ``Discount`` on a Checkout Session carries no ``percent_off`` and no
    ``duration`` at all: they live on the *coupon*, which a Discount only
    reaches through ``discount.source.coupon``, and ``discount.promotion_code``
    arrives as a bare ``promo_…`` id unless it is explicitly expanded. So
    reading the coupon off the session yields ``None`` for every field, the SPA
    renders no discount block, and the feature is silently dead while looking
    perfectly healthy. (Verified against the installed SDK: `_discount.py`
    declares ``promotion_code: ExpandableField[PromotionCode]`` and puts
    ``coupon`` under its nested ``Source`` class.)

    We already fetch the promotion code to turn the student's typed string into
    an id, so expanding its coupon there costs nothing extra and is
    version-independent: it does not matter what shape the session reports.
    """

    #: The ``promo_…`` id that ``checkout.sessions.create``'s ``discounts`` wants.
    id: str
    #: The code as Stripe spells it, so the UI shows canonical casing.
    code: str | None = None
    #: ``None`` when the coupon is an amount-off coupon rather than a percentage.
    percent_off: float | None = None
    #: ``"once"`` | ``"repeating"`` | ``"forever"``.
    duration: str | None = None


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

        The modes are mutually exclusive at the API level — Stripe rejects
        ``success_url``/``cancel_url`` on a non-hosted session and requires
        ``return_url`` instead — so keeping the branch in one place means no
        caller can assemble a session Stripe will refuse.

        For both in-page modes the success destination **is** the hosted
        session's ``success_url``. Reusing it is what keeps the return redirect,
        and therefore the SPA's existing ``?checkout=success`` handling,
        identical across every mode.
        """
        if ui_mode is CheckoutUiMode.EMBEDDED:
            params["ui_mode"] = "embedded_page"
            params["return_url"] = success_url
            # `always` lands the customer on the same URL the hosted flow uses,
            # so every existing success affordance in the SPA still fires.
            params["redirect_on_completion"] = "always"
            params["origin_context"] = "web"
            params["branding_settings"] = self._branding_settings()
        elif ui_mode is CheckoutUiMode.ELEMENTS:
            params["ui_mode"] = "elements"
            params["return_url"] = success_url
            # Deliberately NO `branding_settings`: Stripe answers
            # "`branding_settings` is not supported with `ui_mode: elements`"
            # because the Payment Element is themed client-side instead — which
            # is the entire point, since only that path supports a dark theme.
            # `redirect_on_completion` and `origin_context` are likewise
            # rejected for this mode (both verified against the live API): the
            # redirect is decided by `checkout.confirm()` in the browser.
        else:
            params["success_url"] = success_url
            params["cancel_url"] = cancel_url

    async def resolve_promotion_code(self, code: str) -> ResolvedPromotion | None:
        """Resolve a customer-facing promotion code against Stripe.

        ``checkout.sessions.create`` wants the promotion-code **id**, not the
        human string a student typed, so the code has to be looked up first.
        Stripe matches ``code`` case-insensitively and it is unique among
        *active* codes, so ``active=True`` is what makes an expired or
        deactivated code fail to resolve instead of being applied.

        ``expand=["data.coupon"]`` is what makes the response useful: without it
        ``PromotionCode.coupon`` is a bare ``coupon_…`` id and the percentage and
        duration are unreadable. Reading them here (rather than off the created
        session) is deliberate — see :class:`ResolvedPromotion`.

        ``_stripe_list`` reads the result, NOT ``.data``: ``promotion_codes.list``
        returns a ``ListObject`` envelope, but a bare ``.data`` access on a plain
        list is precisely the pattern that previously broke the downgrade path.

        Returns ``None`` (never raises) when Stripe is unconfigured, the code is
        blank, or nothing active matches. The caller turns ``None`` into a 400 so
        a bad code is surfaced rather than silently charged at full price.
        """
        normalized = code.strip()
        if not self._enabled or not normalized:
            return None

        client = self._get_client()
        result = await asyncio.to_thread(
            client.v1.promotion_codes.list,
            {
                "code": normalized,
                "active": True,
                "limit": 1,
                "expand": ["data.coupon"],
            },
        )
        matches = _stripe_list(result)
        if not matches:
            logger.info("No active Stripe promotion code matched the supplied code")
            return None

        match = matches[0]
        promotion_code_id = _stripe_get(match, "id")
        if not promotion_code_id:
            logger.warning("A matched promotion code carried no id; ignoring")
            return None

        coupon = _stripe_get(match, "coupon")
        percent_off = _stripe_get(coupon, "percent_off")
        duration = _stripe_get(coupon, "duration")
        return ResolvedPromotion(
            id=str(promotion_code_id),
            # Prefer Stripe's own spelling; fall back to what was typed so the
            # UI still has something to show if the field is ever absent.
            code=_stripe_get(match, "code") or normalized,
            percent_off=float(percent_off) if isinstance(percent_off, (int, float)) else None,
            duration=str(duration) if isinstance(duration, str) else None,
        )

    async def create_checkout_session(
        self,
        user_id: str,
        email: str,
        tier: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
        ui_mode: str = CheckoutUiMode.HOSTED,
        promotion: ResolvedPromotion | None = None,
    ) -> CheckoutSessionHandle | None:
        """
        Create a Stripe checkout session for a subscription upgrade.

        Args:
            user_id: The user's internal ID.
            email: The user's email address.
            tier: The target subscription tier.
            success_url: Where to land on success. For an in-page mode this is
                used as ``return_url`` (Stripe rejects ``success_url``).
            cancel_url: Where to land on cancellation. Hosted mode only.
            customer_id: Optional existing Stripe customer ID.
            ui_mode: ``hosted`` (Stripe's page), ``embedded`` (Stripe's embedded
                Checkout on our page) or ``elements`` (the Payment Element on
                our page, which is the one that can be themed dark).
            promotion: Optional promotion to apply as the session's discount.
                This is the **record returned by** :meth:`resolve_promotion_code`,
                not the human code — the id inside it is what Stripe wants, and
                the coupon facts inside it are what the response reports (see
                :class:`ResolvedPromotion` for why they cannot come from the
                created session).

        Returns:
            A handle carrying either the redirect URL or the client secret plus
            the session's totals/discount read-back, or None if Stripe is not
            configured / the tier is not self-serve.
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
            # A 100%-off promotion code makes the total due 0; without this
            # Stripe would still demand a card and the "free month" would not be
            # free. It is set unconditionally because it is harmless at full
            # price — the total is non-zero, so a card is still collected.
            params["payment_method_collection"] = "if_required"
            if promotion is not None:
                # `discounts[].promotion_code` takes the `promo_…` ID, not the
                # human-readable code (resolved beforehand by
                # `resolve_promotion_code`).
                params["discounts"] = [{"promotion_code": promotion.id}]
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
            return self._session_handle(session, resolved_mode, promotion)

        except Exception as e:
            logger.error("Failed to create Stripe checkout session: %s", e, exc_info=True)
            raise

    @staticmethod
    def _first_discount(session: Any) -> Any | None:
        """The session's applied discount, from either SDK field, or None.

        A Checkout Session exposes the applied discount as ``discounts`` — and
        the SDK declares that as a **plain** ``List[Discount]``, not a
        ``{data: []}`` envelope like ``subscription.items``. Older API versions
        use a singular ``discount``. Read both defensively; a session with no
        discount must return None rather than raise.
        """
        singular = _stripe_get(session, "discount")
        if singular is not None:
            return singular
        discounts = _stripe_list(_stripe_get(session, "discounts"))
        return discounts[0] if discounts else None

    @staticmethod
    def _discount_fields(session: Any) -> dict[str, Any]:
        """Best-effort read of the session's applied discount, from the session.

        This is only a FALLBACK. A resolved ``Discount`` cannot carry
        ``percent_off`` or ``duration`` — those live on the coupon, which a
        Discount reaches only through its nested ``source`` object — and
        ``promotion_code`` arrives as a bare ``promo_…`` id unless it was
        explicitly expanded. So every branch here is expected to miss in
        practice; :class:`ResolvedPromotion` is what actually supplies these
        fields. It is kept because it costs nothing and covers the case where
        Stripe does hand back an expanded object.

        Every read goes through ``_stripe_get`` and a missing value becomes
        ``None``, never an exception.
        """
        discount = StripeService._first_discount(session)
        if discount is None:
            return {}
        promotion_code = _stripe_get(discount, "promotion_code")
        code = (
            None
            if isinstance(promotion_code, str)
            else _stripe_get(promotion_code, "code")
        )
        # The coupon hangs off the Discount's nested `source`, not off the
        # Discount itself (verified against the SDK's `_discount.py`).
        coupon = _stripe_get(_stripe_get(discount, "source"), "coupon")
        if coupon is None:
            coupon = _stripe_get(promotion_code, "coupon")
        percent_off = _stripe_get(coupon, "percent_off")
        duration = _stripe_get(coupon, "duration")
        return {
            "discount_code": code,
            "discount_percent_off": percent_off,
            "discount_duration": duration,
        }

    @staticmethod
    def _session_handle(
        session: Any,
        ui_mode: CheckoutUiMode,
        promotion: ResolvedPromotion | None = None,
    ) -> CheckoutSessionHandle:
        """Pick out the one value the browser needs for this mode.

        The two credentials are deliberately exclusive. A hosted session does
        carry a ``client_secret``, but it is useless once the session is hosted,
        and handing the browser a credential it has no use for is exactly the
        kind of thing that ends up in a log line.

        Both in-page modes need the secret: ``embedded_page`` mounts Stripe's
        embedded Checkout with it, ``elements`` mounts the Payment Element with
        it. Only a hosted session needs a URL to navigate to.

        The discount facts come from ``promotion`` (the record already resolved
        for the session create) and fall back to whatever the session reports.
        That order is load-bearing, not a preference — see
        :class:`ResolvedPromotion` for why the session cannot supply them.
        """
        projected: dict[str, Any] = {
            "amount_total": _stripe_get(session, "amount_total"),
            "currency": _stripe_get(session, "currency"),
        }
        projected.update(StripeService._discount_fields(session))
        if promotion is not None:
            # `promotion` wins only where the session had nothing to say, so a
            # future Stripe that does expand the discount is still believed.
            projected["discount_code"] = (
                projected.get("discount_code") or promotion.code
            )
            projected["discount_percent_off"] = (
                projected.get("discount_percent_off")
                if projected.get("discount_percent_off") is not None
                else promotion.percent_off
            )
            projected["discount_duration"] = (
                projected.get("discount_duration") or promotion.duration
            )
        if ui_mode in (CheckoutUiMode.EMBEDDED, CheckoutUiMode.ELEMENTS):
            return CheckoutSessionHandle(
                client_secret=session.client_secret, **projected
            )
        return CheckoutSessionHandle(url=session.url, **projected)

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
            success_url: Where to land on success (used as ``return_url`` for an
                in-page mode, since Stripe rejects ``success_url`` there).
            cancel_url: Where to land on cancellation. Hosted mode only.
            customer_id: Optional existing Stripe customer ID.
            ui_mode: ``hosted`` (Stripe's page), ``embedded`` (Stripe's embedded
                Checkout on our page) or ``elements`` (the Payment Element on
                our page, which is the one that can be themed dark).

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

    async def resume_subscription(self, subscription_id: str) -> bool:
        """
        Undo a scheduled cancellation so the subscription renews again.

        Mirrors :meth:`cancel_subscription`: it only clears the flag on Stripe,
        leaving the tier untouched. The ``customer.subscription.updated``
        webhook stays the single writer for the local tier.

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
                {"cancel_at_period_end": False},
            )
            logger.info("Resumed subscription at period end", extra={"subscription_id": subscription_id})
            return True

        except Exception as e:
            logger.error("Failed to resume subscription: %s", e, exc_info=True)
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
        # ``_stripe_list``, not ``.data``: subscriptions return an envelope
        # today, but the container shape is not something this method should
        # depend on for the wrong reason.
        items = _stripe_list(_stripe_get(subscription, "items"))
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

        Both containers here are plain lists on a schedule (``phases``, and each
        phase's ``items``), unlike the envelopes elsewhere in the API, so they
        are unwrapped with ``_stripe_list`` rather than read as ``.data``.
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

        phases = _stripe_list(_stripe_get(schedule, "phases"))
        if not phases:
            raise ValueError("Stripe subscription schedule has no phases")
        current_phase = phases[0]

        current_items = _stripe_list(_stripe_get(current_phase, "items"))
        if not current_items:
            # Re-declaring the current phase with no items would STRIP the
            # subscription's price once the schedule applies, so refuse instead
            # of sending an empty phase.
            raise ValueError("Stripe subscription schedule phase has no items")
        phase_items: list[Any] = []
        for item in current_items:
            price_id = _price_id(_stripe_get(item, "price"))
            if not price_id:
                # Without an id the SDK would serialize the expanded Price
                # object (every read-only field of it) and Stripe would reject
                # the whole request.
                raise ValueError("Stripe subscription schedule item has no price id")
            phase_items.append(
                {"price": price_id, "quantity": _stripe_get(item, "quantity", 1)}
            )
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

    async def list_invoices(self, customer_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        """List a customer's most recent invoices for the billing history screen.

        Returns an empty list (never raises) when Stripe is unconfigured or the
        call fails: invoices are a read-only convenience, so a Stripe hiccup must
        not turn the billing page into a 500.

        The returned dicts carry the raw unix timestamps (``created``,
        ``period_start``, ``period_end``) rather than datetimes so the mapping to
        the API schema — and the timezone conversion — happens in exactly one
        place, the router.
        """
        if not self._enabled:
            return []

        try:
            client = self._get_client()
            result = await asyncio.to_thread(
                client.v1.invoices.list,
                {"customer": customer_id, "limit": limit},
            )
            data = _stripe_list(result)
        except Exception as e:
            logger.warning("Failed to list invoices: %s", e, exc_info=True)
            return []

        return [
            {
                "id": _stripe_get(invoice, "id"),
                "number": _stripe_get(invoice, "number"),
                "status": _stripe_get(invoice, "status"),
                "amount_paid": _stripe_get(invoice, "amount_paid", 0),
                "amount_due": _stripe_get(invoice, "amount_due", 0),
                "currency": _stripe_get(invoice, "currency", "usd"),
                "created": _stripe_get(invoice, "created"),
                "period_start": _stripe_get(invoice, "period_start"),
                "period_end": _stripe_get(invoice, "period_end"),
                "invoice_pdf": _stripe_get(invoice, "invoice_pdf"),
                "hosted_invoice_url": _stripe_get(invoice, "hosted_invoice_url"),
            }
            for invoice in data
        ]

    async def create_customer_session(self, customer_id: str) -> str | None:
        """Create a Customer Session for managing payment methods in our own UI.

        This is what replaces the Stripe Customer Portal's card-management
        screen with one we control. Stripe's docs recommend exactly this shape:
        manage payment methods "in an account settings or similar page that
        shows existing subscriptions", using a Customer Session so the browser
        can render the Payment Element against the customer's saved methods.

        Only the customer is secret here. The returned ``client_secret`` is
        scoped to this customer and is designed to be handed to the browser, so
        it is never logged.

        Args:
            customer_id: The Stripe customer whose methods may be managed.

        Returns:
            The client secret to mount Stripe.js with, or None when Stripe is
            not configured.
        """
        if not self._enabled:
            logger.warning("Stripe not configured; cannot create customer session")
            return None

        try:
            client = self._get_client()
            session = await asyncio.to_thread(
                client.v1.customer_sessions.create,
                {
                    "customer": customer_id,
                    "components": {
                        "payment_element": {
                            "enabled": True,
                            "features": {
                                # Both features default to "disabled", so
                                # without these the element would show no
                                # saved methods and offer no way to save one —
                                # i.e. the screen would look empty and broken.
                                "payment_method_redisplay": "enabled",
                                "payment_method_save": "enabled",
                                # `off_session` is the correct save usage for
                                # this product: the card is kept so Stripe can
                                # charge the monthly renewal without the
                                # customer present. Leaving it unset would save
                                # the method with the default (on-session)
                                # usage, which is not what a subscription does.
                                # Deliberately NOT `setup_future_usage` as well:
                                # Stripe rejects the pair as an integration
                                # error.
                                "payment_method_save_usage": "off_session",
                                # `payment_method_remove` is deliberately left
                                # at its default. Removing a payment method
                                # detaches it from the customer, which breaks
                                # any active subscription using it — Stripe
                                # warns about this explicitly, so removal stays
                                # off until there is a flow that migrates the
                                # subscription first.
                            },
                        }
                    },
                },
            )
            logger.info(
                # Deliberately no session id: a CustomerSession is ephemeral
                # and carries no `id` at all. The client secret is never logged.
                "Created Stripe customer session",
                extra={"customer_id": customer_id},
            )
            return session.client_secret

        except Exception as e:
            logger.error("Failed to create customer session: %s", e, exc_info=True)
            return None

    # --- Saved payment methods --------------------------------------------
    # The API-key-authenticated, server-side replacement for the Customer
    # Portal's card screen (that portal cannot be branded). ``type="card"``
    # everywhere: the Payment Element can also save SEPA/Link methods, but this
    # screen can only render cards, so list and act on cards alone.

    async def create_setup_intent(self, customer_id: str) -> str | None:
        """Create a SetupIntent so the browser can save a card for later use.

        Stripe's documented shape for saving a method without taking a payment
        is a server-created SetupIntent plus a Customer Session, both handed to
        the Payment Element. The Element *can* create its own intent at
        confirmation time (the "deferred" mode), which is what this app used
        before; creating it here makes the intent explicit up front and means a
        failure to set up surfaces before the customer has typed a card number
        rather than after.

        **``payment_method_types`` is pinned to ``card`` on purpose.** The
        Dashboard configuration advertises every method the account accepts
        (verified live: card, bancontact, klarna, link, blik, pix, satispay),
        and ``automatic_payment_methods`` would offer all of them. But this
        screen lists and manages **cards only** (``type="card"`` on every
        read), so a customer who saved a Klarna or Pix method here would watch
        it vanish from the list immediately afterwards — a save that appears to
        fail. Pinning the type keeps the form and the list describing the same
        set. Apple Pay, Google Pay and Link all ride on the ``card`` type, so
        wallet support is unaffected (confirmed in a browser: the Element
        mounts with the wallet buttons available).

        Returns None on any failure — including Stripe being unconfigured — so
        the caller can fall back to the deferred mode instead of failing the
        whole section.
        """
        if not self._enabled:
            return None

        try:
            client = self._get_client()
            intent = await asyncio.to_thread(
                client.v1.setup_intents.create,
                {
                    "customer": customer_id,
                    # Cards only — see the docstring. The client secret is
                    # designed for the browser and is never logged.
                    "payment_method_types": ["card"],
                },
            )
            secret = _stripe_get(intent, "client_secret")
            logger.info(
                "Created SetupIntent for payment method management",
                extra={"customer_id": customer_id},
            )
            return str(secret) if secret else None

        except Exception as e:
            logger.error("Failed to create setup intent: %s", e, exc_info=True)
            return None

    @staticmethod
    def _payment_method_id(value: Any) -> str | None:
        """The id from a ``default_payment_method`` that may be id or expanded."""
        if isinstance(value, str):
            return value
        return _stripe_get(value, "id")

    @staticmethod
    def _to_saved_payment_method(method: Any, default_id: str | None) -> SavedPaymentMethod:
        """Project a Stripe PaymentMethod onto the fields the UI shows."""
        card = _stripe_get(method, "card")
        method_id = _stripe_get(method, "id")
        # Absent for a card keyed in by hand; `card.wallet` is only populated
        # when the details came from Apple Pay, Google Pay or Link.
        wallet = _stripe_get(_stripe_get(card, "wallet"), "type")
        return SavedPaymentMethod(
            id=str(method_id) if method_id else "",
            brand=str(_stripe_get(card, "brand", "unknown")),
            last4=str(_stripe_get(card, "last4", "")),
            exp_month=int(_stripe_get(card, "exp_month", 0) or 0),
            exp_year=int(_stripe_get(card, "exp_year", 0) or 0),
            is_default=bool(method_id) and method_id == default_id,
            wallet=str(wallet) if wallet else None,
        )

    async def list_payment_methods(
        self, customer_id: str, *, subscription_id: str | None = None
    ) -> list[SavedPaymentMethod]:
        """List the customer's saved cards, marking the default one.

        Returns an empty list (not an error) when Stripe is unconfigured or the
        call fails, so the billing page can render an empty section rather than
        a 500.

        ``is_default`` prefers the **subscription's** ``default_payment_method``
        and falls back to the customer's ``invoice_settings``.
        ``set_default_payment_method`` writes both precisely because they can
        disagree; a subscription created in the Dashboard may already pin its
        own card, and reading only the customer side would then badge a card
        that the next renewal will not charge. A failure to read the
        subscription is tolerated — the customer default is still a real answer.
        """
        if not self._enabled:
            return []

        try:
            client = self._get_client()
            customer = await asyncio.to_thread(
                client.v1.customers.retrieve, customer_id
            )
            default_id = self._payment_method_id(
                _stripe_get(_stripe_get(customer, "invoice_settings"), "default_payment_method")
            )
            if subscription_id:
                subscription_default_id = await self._subscription_default_payment_method(
                    client, subscription_id
                )
                if subscription_default_id:
                    default_id = subscription_default_id
            methods = await asyncio.to_thread(
                client.v1.payment_methods.list,
                {"customer": customer_id, "type": "card"},
            )
            data = _stripe_list(methods)
        except Exception as e:
            logger.error("Failed to list payment methods: %s", e, exc_info=True)
            return []

        return [self._to_saved_payment_method(method, default_id) for method in data]

    @staticmethod
    async def _subscription_default_payment_method(
        client: Any, subscription_id: str
    ) -> str | None:
        """The card a renewal would charge, or None when Stripe cannot say.

        Swallows its own errors: this only refines which card gets the
        "Default" badge, so a transient failure must not take the whole card
        list down with it.
        """
        try:
            subscription = await asyncio.to_thread(
                client.v1.subscriptions.retrieve, subscription_id
            )
        except Exception as e:
            logger.warning(
                "Could not read the subscription's default payment method: %s",
                e,
                extra={"subscription_id": subscription_id},
            )
            return None
        return StripeService._payment_method_id(
            _stripe_get(subscription, "default_payment_method")
        )

    async def set_default_payment_method(
        self,
        customer_id: str,
        payment_method_id: str,
        subscription_id: str | None = None,
    ) -> bool:
        """Make a saved card the default, on the customer AND the subscription.

        Updating the customer alone is not enough. A renewal charges the
        **subscription's** ``default_payment_method``, falling back to the
        customer's only when the subscription has none — and a subscription
        created outside this service (Dashboard, a migration) may already pin
        its own card. Setting only the customer default can therefore leave the
        next invoice charging the OLD card while the UI claims otherwise.
        """
        if not self._enabled:
            return False

        try:
            client = self._get_client()
            await asyncio.to_thread(
                client.v1.customers.update,
                customer_id,
                {"invoice_settings": {"default_payment_method": payment_method_id}},
            )
            if subscription_id:
                await asyncio.to_thread(
                    client.v1.subscriptions.update,
                    subscription_id,
                    {"default_payment_method": payment_method_id},
                )
            logger.info(
                "Set default payment method",
                extra={
                    "customer_id": customer_id,
                    "payment_method_id": payment_method_id,
                    "subscription_id": subscription_id,
                },
            )
            return True

        except Exception as e:
            logger.error("Failed to set default payment method: %s", e, exc_info=True)
            return False

    async def detach_payment_method(self, payment_method_id: str) -> bool:
        """Detach a saved card from its customer.

        The caller MUST already have refused detaching the default card of an
        active subscription. Stripe warns that detaching a payment method used
        by an active subscription breaks that subscription, because it removes
        the customer default the subscription relies on; this method performs
        only the mechanic, never the policy.
        """
        if not self._enabled:
            return False

        try:
            client = self._get_client()
            await asyncio.to_thread(
                client.v1.payment_methods.detach, payment_method_id
            )
            logger.info(
                "Detached payment method", extra={"payment_method_id": payment_method_id}
            )
            return True

        except Exception as e:
            logger.error("Failed to detach payment method: %s", e, exc_info=True)
            return False
