"""Webhook handling API endpoints for Stripe and other integrations."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import current_period_key
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session
from src.infrastructure.logging.audit import log_webhook_failure
from src.presentation.schemas.credit import TransactionType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Stripe events are small (<1 MB in practice). Reject anything larger before
# buffering the body so an unauthenticated caller cannot make the process read
# an arbitrarily large payload (the signature is verified only afterwards).
_MAX_WEBHOOK_BODY_BYTES = 1024 * 1024  # 1 MiB

_TIER_BY_NAME: dict[str, SubscriptionTier] = {
    "pro": SubscriptionTier.PRO,
    "pro_plus": SubscriptionTier.PRO_PLUS,
    "enterprise": SubscriptionTier.ENTERPRISE,
}


def _resolve_tier(tier_name: str | None) -> SubscriptionTier:
    """Strictly resolve a tier name; never silently default to PRO.

    The name-only resolver, kept for the strict callers (and tests) that must
    not fall back to a price. :func:`_resolve_activation_tier` layers the
    price-id fallback on top of the same table.
    """
    tier = _TIER_BY_NAME.get((tier_name or "").lower())
    if tier is None:
        raise ValueError(f"Unrecognised subscription tier: {tier_name!r}")
    return tier


def _tier_for_price_id(price_id: str | None) -> SubscriptionTier | None:
    """Resolve a tier from a Stripe price id, or None when it is not configured.

    This is the fallback for a subscription whose ``metadata.tier`` did not
    survive onto the object the webhook received — most notably a **downgrade**,
    whose new tier is carried by the subscription *schedule*'s second phase and
    is copied onto the subscription by Stripe only at the phase boundary. If
    that copy did not happen, ``metadata.tier`` still names the OLD plan and
    this resolves the new one from the price instead.

    Only the three self-serve prices are recognised, and only by exact match:
    Stripe price ids are opaque, so a missing or unrelated id is simply "no
    fallback", never a guess at another tier.
    """
    if not price_id:
        return None
    settings = get_settings()
    by_price_id = {
        settings.STRIPE_PRICE_PRO: SubscriptionTier.PRO,
        settings.STRIPE_PRICE_PRO_PLUS: SubscriptionTier.PRO_PLUS,
        settings.STRIPE_PRICE_ENTERPRISE: SubscriptionTier.ENTERPRISE,
    }
    return by_price_id.get(price_id)


def _resolve_activation_tier(
    tier_name: str | None, price_id: str | None
) -> SubscriptionTier:
    """Resolve the tier an activation should apply.

    **The price wins when it resolves to a known tier.** The price is what the
    customer is actually being billed, so it is ground truth; ``metadata.tier``
    is a value this service wrote earlier and can be stale. That staleness is
    not hypothetical — it is the exact downgrade case this function exists to
    survive: a downgrade puts the new tier on the subscription *schedule*'s
    second phase, and Stripe copies that onto the subscription only at the
    phase boundary. If the copy does not happen, ``metadata.tier`` still names
    the OLD plan, so a metadata-first order would re-apply the old tier and the
    downgrade would silently never take effect.

    Metadata is still consulted when the price is absent or unrecognised (a
    legacy or non-self-serve price is simply "no information", never a guess),
    which keeps the checkout path working — a `checkout.session.completed`
    payload carries only a subscription *id*, so there is no price to read and
    the metadata written at checkout is all there is.

    Disagreement is logged at WARNING: it means one of the two sources is
    wrong, and which one is genuinely ambiguous, so it should be visible rather
    than silently resolved.

    Raises ``ValueError`` when neither source resolves — the existing
    "skip and log" path for callers. ``tier_name`` is deliberately NOT
    defaulted to ``"pro"`` upstream: doing so would make every unrecognised
    payload silently re-apply PRO.
    """
    from_price = _tier_for_price_id(price_id)
    from_metadata = _TIER_BY_NAME.get((tier_name or "").lower())

    if from_price is not None:
        if from_metadata is not None and from_metadata is not from_price:
            logger.warning(
                "Tier sources disagree; trusting the billed price. "
                "metadata.tier=%r price_id=%r resolved=%r",
                tier_name,
                price_id,
                from_price,
            )
        return from_price

    if from_metadata is not None:
        return from_metadata

    raise ValueError(
        f"Unrecognised subscription tier: {tier_name!r} (price id {price_id!r})"
    )


def _first_price_id(subscription_data: Any) -> str | None:
    """Read ``items.data[0].price.id`` from a subscription payload, or None.

    The path is four levels deep and any level may be missing on a partial or
    odd payload, so every step is guarded: a payload without a price must
    degrade to "no fallback available", never raise an unexpected exception out
    of a webhook.
    """
    data = _normalise_event_data(subscription_data)
    items = data.get("items")
    if not isinstance(items, dict):
        return None
    entries = items.get("data")
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    price = first.get("price")
    if not isinstance(price, dict):
        return None
    price_id = price.get("id")
    return price_id if isinstance(price_id, str) else None


async def _activate_subscription(
    db: AsyncSession,
    *,
    user_id: str,
    tier_name: str | None,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    price_id: str | None = None,
) -> None:
    """Upsert the user's subscription row and grant the tier's monthly credits."""
    tier = _resolve_activation_tier(tier_name, price_id)
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
        period_key = current_period_key()
        credit_repo = SQLCreditRepository(db)
        existing = await credit_repo.get_credit(user_id, period_key)
        if existing is not None:
            # This row is the PLAN bucket only. Purchased credits and carryover
            # live on ``user_subscriptions`` (``purchased_credits`` /
            # ``carryover_credits``) and this handler must never touch them —
            # see the comment below for why that is non-negotiable.
            #
            # Raise the plan bucket to this tier's monthly grant and add the
            # difference to the unspent balance; never lower either field. This
            # is what keeps the handler idempotent: Stripe retries deliveries,
            # and ``invoice.payment_succeeded`` re-applies the tier on EVERY
            # monthly renewal, so re-applying the same tier (delta == 0) must be
            # a no-op rather than a top-up.
            #
            # An explicit upgrade is NOT handled here. It is performed by
            # ``POST /subscription/change-plan``, which converts the unspent
            # plan balance into expiring carryover and resets the plan bucket to
            # the new grant. By the time this handler sees the new tier the
            # bucket already holds that grant, so ``delta`` is 0 and nothing is
            # added. Compute the upgrade here instead (2000 + 320 style carry)
            # and the carryover would be double-counted.
            #
            # A legacy row created before the wallet split can still carry
            # purchased credits merged into ``allowance``; that makes ``delta``
            # non-positive and the branch a no-op, which is the safe direction:
            # destroying something a user paid for is far worse than leaving a
            # downgraded account holding a few extra.
            delta = allowance - existing.allowance
            if delta > 0:
                existing.allowance = allowance
                existing.remaining = min(existing.remaining + delta, allowance)
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


async def _grant_purchased_credits(
    db: AsyncSession,
    *,
    user_id: str,
    credits: int,
    reference_id: str | None,
) -> None:
    """Grant purchased credits idempotently after payment confirms.

    Uses the Stripe session id as the transaction reference so repeated
    deliveries of the same webhook don't double-credit the user. Purchased
    credits are stored on the subscription row's wallet (``purchased_credits``),
    never in the plan bucket: they do not expire, whereas the plan grant is
    re-applied every renewal.

    Idempotency is enforced by a UNIQUE constraint on ``credit_transactions.
    reference_id``: a duplicate delivery attempts to insert a transaction with
    the same reference, which raises ``IntegrityError`` and is treated as an
    already-granted credit (race-safe — no TOCTOU window). The wallet bump and
    the ledger insert share one transaction, so the losing delivery rolls back
    cleanly.
    """
    if not reference_id:
        logger.warning("Credit purchase webhook missing reference_id; ignoring")
        return
    if credits < 1 or credits > 100000:
        # Bound the granted amount to prevent metadata-driven abuse.
        logger.warning("Rejecting out-of-range credit grant: %s credits", credits)
        return

    credit_repo = SQLCreditRepository(db)
    sub_repo = SQLSubscriptionRepository(db)

    # Quick pre-check (non-authoritative) to avoid the write path for the common
    # duplicate case. The unique constraint is the authoritative guard.
    existing_txns = await credit_repo.list_transactions(int(user_id), offset=0, limit=200)
    if any(
        t.transaction_type == TransactionType.PURCHASE.value
        and t.reference_id == reference_id
        for t in existing_txns
    ):
        logger.info("Duplicate credit purchase webhook ignored: ref=%s", reference_id)
        return

    # Purchased credits live on the subscription row's wallet, NOT in the plan
    # bucket: they never expire, while the plan bucket is re-granted on every
    # renewal. Merging them (the old behaviour) is what forced the webhook to
    # guess whether a large allowance meant "the user bought credits".
    row = await sub_repo.get_subscription_row(int(user_id))
    if row is None:
        # A FREE account that has never needed a subscription row: create one
        # so the wallet columns have somewhere to live. Only when absent —
        # ``upsert_subscription`` sets the tier unconditionally, so calling it
        # on an existing row would downgrade a paying user to FREE.
        await sub_repo.upsert_subscription(
            actor_key=f"user:{user_id}",
            user_id=int(user_id),
            tier=SubscriptionTier.FREE,
        )
        row = await sub_repo.get_subscription_row(int(user_id))
    if row is None:  # pragma: no cover - defensive
        logger.error("Could not create a wallet row for user=%s; purchase ignored", user_id)
        return

    row.purchased_credits += credits
    row.updated_at = datetime.now(UTC)

    try:
        # One transaction: this commit flushes the wallet bump AND inserts the
        # ledger row. That fixes the latent bug where ``save_credit`` committed
        # the bump before the unique-constraint check, so the ``rollback`` on a
        # duplicate could not undo it. Now a duplicate rolls back the bump with
        # the rejected insert.
        await credit_repo.record_transaction(
            transaction_id=str(uuid.uuid4()),
            user_id=int(user_id),
            amount=credits,
            transaction_type=TransactionType.PURCHASE.value,
            reference_id=reference_id,
            description=f"Purchased {credits} credits via Stripe",
        )
    except IntegrityError:
        # A concurrent/duplicate delivery already recorded this reference. The
        # unique constraint fired; roll back so the wallet bump above is undone
        # in the same transaction.
        await db.rollback()
        logger.info("Duplicate credit purchase ignored (unique ref): %s", reference_id)
        return

    logger.info("Granted %s purchased credits to user=%s ref=%s", credits, user_id, reference_id)


async def _downgrade_to_free(db: AsyncSession, user_id: str) -> None:
    """Downgrade a user to the FREE tier after their subscription ends."""
    sub_repo = SQLSubscriptionRepository(db)
    await sub_repo.upsert_subscription(
        actor_key=f"user:{user_id}",
        user_id=int(user_id),
        tier=SubscriptionTier.FREE,
    )
    logger.info("User %s downgraded to FREE tier", user_id)


def _normalise_event_data(event_data: Any) -> dict[str, Any]:
    """Return a plain dict for Stripe event data (handles StripeObject)."""
    if hasattr(event_data, "to_dict_recursive"):
        return event_data.to_dict_recursive()
    if hasattr(event_data, "to_dict"):
        return event_data.to_dict()
    return dict(event_data) if isinstance(event_data, dict) else {}


def _handled_credit_purchase_metadata(metadata: dict[str, Any]) -> tuple[str | None, int]:
    """Extract user_id and credits from the checkout session metadata."""
    user_id = metadata.get("user_id")
    credits = metadata.get("credits")
    try:
        credits_int = int(credits) if credits is not None else 0
    except (TypeError, ValueError):
        credits_int = 0
    return user_id, credits_int


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def handle_stripe_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """
    Handle incoming Stripe webhook events.

    Verifies the webhook signature and processes events such as:
    - checkout.session.completed (subscription activation + credit purchase)
    - checkout.session.async_payment_succeeded / async_payment_failed
    - invoice.payment_succeeded / invoice.payment_failed
    - customer.subscription.updated / customer.subscription.deleted
    """
    settings = get_settings()

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Stripe webhook secret not configured",
        )

    # Reject an oversized body before reading it. Requests without a
    # Content-Length header (e.g. chunked) are not pre-rejected so a legitimate
    # chunked client keeps working.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > _MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload too large",
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
        log_webhook_failure(provider="stripe", reason=str(e))
        logger.error("Stripe webhook signature verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    event_type = event.type
    event_data = _normalise_event_data(event.data.object)

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, event_data)

    elif event_type in ("checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed"):
        # Same handling as .completed; fulfilment logic checks payment_status.
        await _handle_checkout_completed(db, event_data)

    elif event_type == "invoice.payment_succeeded":
        await _refresh_allowance_for_invoice(db, event_data)

    elif event_type == "invoice.payment_failed":
        logger.info("Invoice payment failed: %s", event.id)

    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(db, event_data)

    elif event_type == "customer.subscription.deleted":
        metadata = event_data.get("metadata", {}) or {}
        user_id = metadata.get("user_id")
        if user_id:
            await _downgrade_to_free(db, user_id)

    return {"status": "received", "type": event_type}


async def _handle_checkout_completed(db: AsyncSession, event_data: dict[str, Any]) -> None:
    metadata = event_data.get("metadata", {}) or {}
    payment_status = event_data.get("payment_status")
    # For delayed-notification methods, only fulfill when the payment actually
    # succeeded (payment_status != 'unpaid').
    if payment_status == "unpaid":
        logger.info("Checkout session unpaid; skipping fulfillment: %s", event_data.get("id"))
        return

    kind = metadata.get("kind")
    if kind == "credit_purchase":
        user_id, credits = _handled_credit_purchase_metadata(metadata)
        if user_id and credits:
            await _grant_purchased_credits(
                db,
                user_id=user_id,
                credits=credits,
                reference_id=event_data.get("id"),
            )
        return

    # Otherwise treat as a subscription activation.
    user_id = metadata.get("user_id")
    tier = metadata.get("tier", "pro")
    if user_id:
        try:
            await _activate_subscription(
                db,
                user_id=user_id,
                tier_name=tier,
                stripe_customer_id=event_data.get("customer"),
                stripe_subscription_id=event_data.get("subscription"),
            )
        except ValueError as exc:
            logger.warning("Skipping subscription activation: %s", exc)


async def _refresh_allowance_for_invoice(db: AsyncSession, event_data: dict[str, Any]) -> None:
    """Refresh the user's monthly conversion-credit allowance on renewal.

    Invoices carry a ``subscription`` reference; that subscription's metadata
    holds the ``user_id`` and ``tier`` we stored at checkout. We re-fetch the
    subscription to read those values and re-apply the tier allowance.
    """
    subscription_id = event_data.get("subscription")
    if not subscription_id:
        logger.info("Invoice payment succeeded without subscription: %s", event_data.get("id"))
        return

    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        logger.warning("Cannot refresh allowance: Stripe secret not configured")
        return

    try:
        from src.infrastructure.adapters.payment.stripe_service import StripeService
        client = StripeService()._get_client()
        # The Stripe SDK is synchronous; run it off the event loop so the
        # webhook does not block the whole server for a network round-trip.
        sub = await asyncio.to_thread(client.v1.subscriptions.retrieve, subscription_id)
    except Exception as e:
        logger.error("Failed to refresh allowance from subscription: %s", e, exc_info=True)
        return

    metadata = getattr(sub, "metadata", None) or {}
    user_id = metadata.get("user_id")
    # Not defaulted to "pro": an absent tier must reach the price-id fallback
    # inside ``_activate_subscription`` rather than silently re-applying PRO.
    tier_name = metadata.get("tier")
    if not user_id:
        logger.info("Invoice subscription has no user metadata: %s", subscription_id)
        return

    try:
        await _activate_subscription(
            db,
            user_id=user_id,
            tier_name=tier_name,
            stripe_customer_id=event_data.get("customer"),
            stripe_subscription_id=subscription_id,
            price_id=_first_price_id(sub),
        )
    except ValueError as exc:
        logger.warning("Skipping invoice allowance refresh: %s", exc)


async def _handle_subscription_updated(db: AsyncSession, event_data: dict[str, Any]) -> None:
    metadata = event_data.get("metadata", {}) or {}
    user_id = metadata.get("user_id")
    if not user_id:
        logger.info("Subscription updated without user metadata: %s", event_data.get("id"))
        return

    status = event_data.get("status")
    if status == "canceled":
        await _downgrade_to_free(db, user_id)
        return

    # Not defaulted to "pro": an absent tier must reach the price-id fallback
    # inside ``_activate_subscription`` rather than silently re-applying PRO.
    tier_name = metadata.get("tier")
    try:
        await _activate_subscription(
            db,
            user_id=user_id,
            tier_name=tier_name,
            stripe_customer_id=event_data.get("customer"),
            # NOTE: this handler is the odd one out. A
            # `customer.subscription.updated` payload IS the subscription
            # object, so its own id lives at `event_data["id"]` — there is no
            # nested `subscription` field, and reading one (the old code) stored
            # NULL, silently breaking `cancel_subscription` and the status
            # endpoint's period lookup. The sibling handlers intentionally keep
            # `.subscription`: there the payload is a Checkout Session / Invoice
            # and `.subscription` is the correct NESTED reference. Do not
            # "unify" these three lines.
            stripe_subscription_id=event_data.get("id"),
            price_id=_first_price_id(event_data),
        )
    except ValueError as exc:
        logger.warning("Skipping subscription update: %s", exc)
    logger.info("Subscription updated: user=%s status=%s", user_id, status)


@router.get("/health", status_code=status.HTTP_200_OK)
async def webhook_health() -> dict:
    """Health check endpoint for webhook consumers."""
    return {"status": "healthy"}
