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
from src.domain.subscriptions.policies.payment_grace import (
    grace_deadline,
    parse_holidays,
)
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    ensure_utc,
)
from src.domain.subscriptions.value_object.payment_state import (
    is_entitling,
    is_terminal,
    opens_grace,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import StripeWebhookEventModel
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
    stripe_status: str = "active",
    event_created: datetime | None = None,
    current_period_end: datetime | None = None,
    cancel_at_period_end: bool | None = None,
) -> None:
    """Upsert the user's subscription row and grant the tier's monthly credits.

    Callers must only reach this for a subscription Stripe reports as paid or
    trialing. It ends any grace window, because a settled payment is the only
    evidence that restores entitlement.
    """
    tier = _resolve_activation_tier(tier_name, price_id)
    actor_key = f"user:{user_id}"

    sub_repo = SQLSubscriptionRepository(db)
    await sub_repo.upsert_subscription(
        actor_key=actor_key,
        user_id=int(user_id),
        tier=tier,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        stripe_status=stripe_status,
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

    # A settled payment is what ends a grace window. ``clear_grace`` is a no-op
    # for a row already in OK, and order-guarded so a stale success cannot
    # resurrect a subscription that a newer failure has since suspended.
    await sub_repo.clear_grace(
        user_id=int(user_id),
        event_created=event_created,
        stripe_status=stripe_status,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
    )
    # Period/cancel flags are mirrored regardless of the grace transition so the
    # status endpoint and reconciliation do not depend on a live Stripe call.
    await sub_repo.mirror_stripe_state(
        int(user_id),
        stripe_status=stripe_status,
        current_period_end=current_period_end,
        cancel_at_period_end=cancel_at_period_end,
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


async def _downgrade_to_free(
    db: AsyncSession,
    user_id: str,
    *,
    stripe_status: str = "canceled",
    event_created: datetime | None = None,
) -> None:
    """Move a user to FREE because their subscription has ended.

    ``stripe_subscription_id`` is deliberately retained: recovery and
    reconciliation need it, and clearing it was what made ``/change-plan`` 500
    for an ex-customer (the route used its presence as the "has a subscription"
    test). Ending a subscription is not a payment failure, so the grace columns
    are cleared rather than opened.
    """
    sub_repo = SQLSubscriptionRepository(db)
    await sub_repo.upsert_subscription(
        actor_key=f"user:{user_id}",
        user_id=int(user_id),
        tier=SubscriptionTier.FREE,
        stripe_status=stripe_status,
    )
    await sub_repo.clear_grace(
        user_id=int(user_id),
        event_created=event_created,
        stripe_status=stripe_status,
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


def _unix_to_datetime(value: Any) -> datetime | None:
    """Convert a Stripe unix-seconds field to an aware UTC datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _first_period_end(subscription_data: Any) -> datetime | None:
    """Read ``items.data[0].current_period_end`` as a UTC datetime.

    Stripe moved ``current_period_start``/``current_period_end`` off the
    Subscription object and onto its **items** in this API version. Reading the
    subscription-level field silently yields ``None``, which is why the old
    ``previous_period_end`` carry-over expiry was always null. Every level is
    guarded: a partial payload degrades to "no information", never raises.
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
    return _unix_to_datetime(first.get("current_period_end"))


# --- Webhook event ledger -------------------------------------------------
# Durable idempotency. Before this existed, "was this event processed?" could not
# be answered: the endpoint answered 200 from many paths that wrote nothing.
# Stripe's event id is unique, so the primary key is the dedupe constraint.


async def _claim_event(
    db: AsyncSession,
    *,
    event_id: str | None,
    event_type: str,
    event_created: int | None,
) -> bool:
    """Insert the event, returning False when it was already processed.

    Commits immediately so the claim survives a crash mid-handler: a partially
    processed event is visible as ``received`` rather than vanishing. A row left
    ``received``/``failed`` by an earlier attempt is **retryable**, so Stripe's
    retry after a genuine handler error can reprocess it.
    """
    if not event_id:
        # No id means no idempotency possible. Let the handler run rather than
        # dropping a real fulfilment.
        logger.warning("Stripe event without an id; processing without dedupe")
        return True

    existing = await db.get(StripeWebhookEventModel, event_id)
    if existing is not None:
        if existing.outcome.startswith(("processed", "duplicate", "skipped")):
            return False
        # A previous attempt crashed or failed: allow it to run again.
        existing.outcome = "received"
        existing.error = None
        await db.commit()
        return True

    try:
        db.add(
            StripeWebhookEventModel(
                event_id=event_id,
                event_type=event_type,
                event_created=event_created,
            )
        )
        await db.commit()
        return True
    except IntegrityError:
        # A concurrent delivery claimed it first.
        await db.rollback()
        return False


async def _record_event_outcome(
    db: AsyncSession, *, event_id: str | None, outcome: str, error: str | None = None
) -> None:
    """Best-effort finish marker on the claimed event row."""
    if not event_id:
        return
    try:
        row = await db.get(StripeWebhookEventModel, event_id)
        if row is not None:
            row.outcome = outcome[:64]
            row.error = (error[:2000] if error else None)
            await db.commit()
    except Exception:  # pragma: no cover - bookkeeping must never fail a delivery
        await db.rollback()
        logger.warning("Could not record webhook outcome for %s", event_id)


async def _resolve_subscription_row(
    db: AsyncSession,
    *,
    subscription_id: str | None,
    customer_id: str | None,
):
    """Find the local row for an invoice/subscription event.

    Matching the **subscription id** first is load-bearing: a late event for a
    subscription the customer has already replaced must not be applied to the
    current one. The customer id is a fallback for events that predate the
    subscription id being stored.
    """
    repo = SQLSubscriptionRepository(db)
    if subscription_id:
        row = await repo.get_by_stripe_subscription_id(subscription_id)
        if row is not None:
            return row
    if customer_id:
        return await repo.get_by_stripe_customer_id(customer_id)
    return None


async def _stripe_subscription_snapshot(subscription_id: str) -> dict[str, Any] | None:
    """Retrieve a subscription from Stripe, returning the fields we use.

    Kept in one place so every caller reads periods from the item (where they
    actually live) rather than from the subscription object (where they are
    ``None``).
    """
    settings = get_settings()
    if not settings.STRIPE_SECRET_KEY:
        return None
    try:
        from src.infrastructure.adapters.payment.stripe_service import StripeService

        client = StripeService()._get_client()
        # The Stripe SDK is synchronous; run it off the event loop.
        sub = await asyncio.to_thread(client.v1.subscriptions.retrieve, subscription_id)
    except Exception as exc:
        logger.error(
            "Failed to retrieve Stripe subscription %s: %s", subscription_id, exc
        )
        return None
    return {
        "id": getattr(sub, "id", subscription_id),
        "status": getattr(sub, "status", None),
        "metadata": getattr(sub, "metadata", None) or {},
        "customer": getattr(sub, "customer", None),
        "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
        "current_period_end": _first_period_end(sub),
        "price_id": _first_price_id(sub),
    }


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
    event_id = getattr(event, "id", None)
    event_created = _unix_to_datetime(getattr(event, "created", None))

    # Durable idempotency. Stripe retries deliveries, and a retry of an event
    # that already succeeded must not be applied twice. The claim is committed
    # before the handler runs, so a crash leaves a visible "received" row rather
    # than silence; a retry of a *failed* event is allowed to reprocess.
    if not await _claim_event(
        db,
        event_id=event_id,
        event_type=event_type,
        event_created=getattr(event, "created", None),
    ):
        logger.info("Duplicate Stripe webhook ignored: id=%s type=%s", event_id, event_type)
        return {"status": "duplicate", "type": event_type}

    outcome = "processed"
    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(db, event_data, event_created=event_created)

        elif event_type == "checkout.session.async_payment_succeeded":
            await _handle_checkout_completed(db, event_data, event_created=event_created)

        elif event_type == "checkout.session.async_payment_failed":
            # A failed asynchronous payment must never fulfil. The subscription's
            # own status events carry the recovery path.
            outcome = "skipped:async_payment_failed"
            logger.info("Async checkout payment failed: %s", event_data.get("id"))

        elif event_type in ("invoice.payment_succeeded", "invoice.paid"):
            await _refresh_allowance_for_invoice(db, event_data, event_created=event_created)

        elif event_type in ("invoice.payment_failed", "invoice.payment_action_required"):
            # ``payment_action_required`` means SCA/3DS is outstanding: the
            # invoice is unpaid, so it starts the same bounded clock rather than
            # leaving paid access running with no deadline at all.
            await _handle_invoice_payment_failed(db, event_data, event_created=event_created)

        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(db, event_data, event_created=event_created)

        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(db, event_data, event_created=event_created)

        else:
            outcome = "skipped:unhandled"
    except Exception as exc:
        # Record the failure, then re-raise so FastAPI answers non-2xx and
        # Stripe retries. The claim row is left in a retryable state.
        await _record_event_outcome(db, event_id=event_id, outcome="failed", error=str(exc))
        raise

    await _record_event_outcome(db, event_id=event_id, outcome=outcome)
    return {"status": "received", "type": event_type}


async def _handle_checkout_completed(
    db: AsyncSession,
    event_data: dict[str, Any],
    *,
    event_created: datetime | None = None,
) -> None:
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
    # Deliberately NOT defaulted to "pro": an absent tier must reach the
    # price-id fallback inside ``_activate_subscription`` (or be skipped),
    # never silently re-apply PRO.
    tier = metadata.get("tier")
    if user_id:
        try:
            await _activate_subscription(
                db,
                user_id=user_id,
                tier_name=tier,
                stripe_customer_id=event_data.get("customer"),
                stripe_subscription_id=event_data.get("subscription"),
                stripe_status="active",
                event_created=event_created,
            )
        except ValueError as exc:
            logger.warning("Skipping subscription activation: %s", exc)


async def _handle_invoice_payment_failed(
    db: AsyncSession,
    event_data: dict[str, Any],
    *,
    event_created: datetime | None = None,
) -> None:
    """Open (or continue) the payment grace window for a failed invoice.

    The window is anchored to the **invoice's** creation instant so Stripe's
    dunning retries on the same invoice cannot extend it, and the transition
    itself is guarded on state so a replay cannot open a second window.
    """
    subscription_id = event_data.get("subscription")
    customer_id = event_data.get("customer")
    invoice_id = event_data.get("id")
    # ``created`` is stable across retries of the same invoice; ``event.created``
    # moves on every retry, so it is only a fallback.
    anchor = (
        _unix_to_datetime(event_data.get("created"))
        or event_created
        or datetime.now(UTC)
    )

    row = await _resolve_subscription_row(
        db, subscription_id=subscription_id, customer_id=customer_id
    )
    user_id = str(row.user_id) if row is not None and row.user_id is not None else None

    if user_id is None and subscription_id:
        snapshot = await _stripe_subscription_snapshot(subscription_id)
        if snapshot is not None:
            metadata_user = (snapshot.get("metadata") or {}).get("user_id")
            if metadata_user:
                user_id = str(metadata_user)

    if user_id is None:
        logger.warning(
            "Payment failure could not be attributed to a user: invoice=%s subscription=%s",
            invoice_id,
            subscription_id,
        )
        return

    # Nothing to protect for an account that is already on a non-paid plan.
    if row is not None and row.tier in (SubscriptionTier.FREE, SubscriptionTier.GUEST):
        logger.info(
            "Payment failure on a non-paid plan; no grace window opened: user=%s", user_id
        )
        return

    settings = get_settings()
    deadline = grace_deadline(
        anchor,
        business_days=settings.PAYMENT_GRACE_BUSINESS_DAYS,
        timezone=settings.PAYMENT_GRACE_TIMEZONE,
        holidays=parse_holidays(settings.PAYMENT_GRACE_HOLIDAYS),
    )

    sub_repo = SQLSubscriptionRepository(db)
    opened = await sub_repo.open_grace(
        user_id=int(user_id),
        anchor=anchor,
        deadline=deadline,
        invoice_id=invoice_id,
        event_created=event_created,
        stripe_status="past_due",
    )
    if opened:
        logger.warning(
            "Payment failed; grace window opened: user=%s invoice=%s deadline=%s",
            user_id,
            invoice_id,
            deadline.isoformat(),
        )
    else:
        # Already in grace, revoked, or a stale event. Record the attempt but
        # never move the existing deadline.
        await sub_repo.touch_payment_failure(
            user_id=int(user_id),
            failed_at=anchor,
            event_created=event_created,
        )
        logger.info(
            "Additional payment failure; existing state retained: user=%s invoice=%s",
            user_id,
            invoice_id,
        )


async def _refresh_allowance_for_invoice(
    db: AsyncSession,
    event_data: dict[str, Any],
    *,
    event_created: datetime | None = None,
) -> None:
    """Refresh the user's monthly allowance after a paid invoice.

    The subscription is re-fetched because an invoice payload carries only its
    id. Its **status** is then authoritative: a ``payment_succeeded`` for an
    invoice that was paid before a cancellation must not resurrect a cancelled
    subscription, which is exactly what the previous unconditional activation
    did.
    """
    subscription_id = event_data.get("subscription")
    if not subscription_id:
        logger.info("Invoice payment succeeded without subscription: %s", event_data.get("id"))
        return

    snapshot = await _stripe_subscription_snapshot(subscription_id)
    if snapshot is None:
        logger.warning(
            "Cannot confirm subscription %s; leaving entitlement unchanged", subscription_id
        )
        return

    status = (snapshot.get("status") or "").lower()
    metadata = snapshot.get("metadata") or {}
    user_id = metadata.get("user_id")
    if not user_id:
        logger.info("Invoice subscription has no user metadata: %s", subscription_id)
        return

    sub_repo = SQLSubscriptionRepository(db)

    if is_terminal(status):
        await _downgrade_to_free(
            db, str(user_id), stripe_status=status, event_created=event_created
        )
        return

    if opens_grace(status):
        # Another invoice on this subscription is still unpaid. Do not clear
        # grace just because *this* invoice settled.
        settings = get_settings()
        anchor = event_created or datetime.now(UTC)
        await sub_repo.open_grace(
            user_id=int(user_id),
            anchor=anchor,
            deadline=grace_deadline(
                anchor,
                business_days=settings.PAYMENT_GRACE_BUSINESS_DAYS,
                timezone=settings.PAYMENT_GRACE_TIMEZONE,
                holidays=parse_holidays(settings.PAYMENT_GRACE_HOLIDAYS),
            ),
            invoice_id=event_data.get("id"),
            event_created=event_created,
            stripe_status=status,
        )
        return

    if not is_entitling(status):
        # Unknown status: mirror it, but never grant.
        await sub_repo.mirror_stripe_state(
            int(user_id),
            stripe_status=status or None,
            current_period_end=snapshot.get("current_period_end"),
            cancel_at_period_end=snapshot.get("cancel_at_period_end"),
        )
        return

    try:
        await _activate_subscription(
            db,
            user_id=str(user_id),
            # Not defaulted to "pro": an absent tier must reach the price-id
            # fallback rather than silently re-applying PRO.
            tier_name=metadata.get("tier"),
            stripe_customer_id=snapshot.get("customer") or event_data.get("customer"),
            stripe_subscription_id=subscription_id,
            price_id=snapshot.get("price_id"),
            stripe_status=status,
            event_created=event_created,
            current_period_end=snapshot.get("current_period_end"),
            cancel_at_period_end=snapshot.get("cancel_at_period_end"),
        )
    except ValueError as exc:
        logger.warning("Skipping invoice allowance refresh: %s", exc)


async def _handle_subscription_updated(
    db: AsyncSession,
    event_data: dict[str, Any],
    *,
    event_created: datetime | None = None,
) -> None:
    """Apply a subscription status change to the local lifecycle.

    The status decides the transition. The old implementation treated every
    status except ``canceled`` as a successful activation, which meant the two
    events that signal non-payment (``past_due``, ``unpaid``) *re-granted* the
    paid tier.
    """
    subscription_id = event_data.get("id")
    metadata = event_data.get("metadata", {}) or {}
    status = (event_data.get("status") or "").lower()
    period_end = _first_period_end(event_data)
    cancel_at_period_end = bool(event_data.get("cancel_at_period_end", False))

    row = await _resolve_subscription_row(
        db, subscription_id=subscription_id, customer_id=event_data.get("customer")
    )
    user_id = metadata.get("user_id") or (
        str(row.user_id) if row is not None and row.user_id is not None else None
    )
    if not user_id:
        logger.warning(
            "Subscription update without a resolvable user: %s", subscription_id
        )
        return

    # Ordering guard, applied before ANY write. A stale delivery must not even
    # reach ``_activate_subscription`` — otherwise it could top up the plan
    # bucket for a subscription whose newer state we already recorded.
    stored_event_at = ensure_utc(row.last_payment_event_at) if row is not None else None
    if (
        stored_event_at is not None
        and event_created is not None
        and event_created < stored_event_at
    ):
        logger.info(
            "Ignoring stale subscription event: event_created=%s stored=%s",
            event_created.isoformat(),
            stored_event_at.isoformat(),
        )
        return

    # A late event for a subscription the customer has already replaced must not
    # touch the current one. This is the guard that stops an old cancellation
    # from downgrading a paying customer.
    if (
        row is not None
        and row.stripe_subscription_id
        and subscription_id
        and row.stripe_subscription_id != subscription_id
    ):
        logger.info(
            "Ignoring update for a superseded subscription: event=%s stored=%s",
            subscription_id,
            row.stripe_subscription_id,
        )
        return

    sub_repo = SQLSubscriptionRepository(db)

    if status in ("canceled", "incomplete_expired", "incomplete"):
        await _downgrade_to_free(
            db, str(user_id), stripe_status=status, event_created=event_created
        )
        return

    if opens_grace(status):
        # Keep the plan, start (or continue) the bounded window. The read path
        # enforces the deadline even if this never runs again.
        anchor = event_created or datetime.now(UTC)
        settings = get_settings()
        deadline = grace_deadline(
            anchor,
            business_days=settings.PAYMENT_GRACE_BUSINESS_DAYS,
            timezone=settings.PAYMENT_GRACE_TIMEZONE,
            holidays=parse_holidays(settings.PAYMENT_GRACE_HOLIDAYS),
        )
        opened = await sub_repo.open_grace(
            user_id=int(user_id),
            anchor=anchor,
            deadline=deadline,
            invoice_id=None,
            event_created=event_created,
            stripe_status=status,
        )
        if not opened:
            await sub_repo.touch_payment_failure(
                user_id=int(user_id),
                failed_at=anchor,
                event_created=event_created,
                stripe_status=status,
            )
        await sub_repo.mirror_stripe_state(
            int(user_id),
            stripe_status=status,
            current_period_end=period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        logger.info(
            "Subscription %s is in payment recovery: user=%s", status, user_id
        )
        return

    if is_entitling(status):
        try:
            await _activate_subscription(
                db,
                user_id=str(user_id),
                tier_name=metadata.get("tier"),
                stripe_customer_id=event_data.get("customer"),
                # NOTE: this handler is the odd one out. A
                # `customer.subscription.updated` payload IS the subscription
                # object, so its own id lives at `event_data["id"]` — there is no
                # nested `subscription` field. Do not "unify" this with the
                # sibling handlers, where the payload is a Session/Invoice.
                stripe_subscription_id=subscription_id,
                price_id=_first_price_id(event_data),
                stripe_status=status,
                event_created=event_created,
                current_period_end=period_end,
                cancel_at_period_end=cancel_at_period_end,
            )
        except ValueError as exc:
            logger.warning("Skipping subscription update: %s", exc)
        logger.info("Subscription updated: user=%s status=%s", user_id, status)
        return

    # Unknown/absent status: mirror only. Never grant, never revoke on a value
    # we do not understand.
    await sub_repo.mirror_stripe_state(
        int(user_id),
        stripe_status=status or None,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
    )
    logger.warning(
        "Subscription update with an unrecognised status: status=%r user=%s",
        status,
        user_id,
    )


async def _handle_subscription_deleted(
    db: AsyncSession,
    event_data: dict[str, Any],
    *,
    event_created: datetime | None = None,
) -> None:
    """End a subscription locally.

    Guarded on the subscription id: deleting a superseded subscription must not
    downgrade an account that is paying for a newer one.
    """
    subscription_id = event_data.get("id")
    metadata = event_data.get("metadata", {}) or {}

    row = await _resolve_subscription_row(
        db, subscription_id=subscription_id, customer_id=event_data.get("customer")
    )
    user_id = metadata.get("user_id") or (
        str(row.user_id) if row is not None and row.user_id is not None else None
    )
    if not user_id:
        logger.warning(
            "Subscription deleted without a resolvable user: %s", subscription_id
        )
        return

    if (
        row is not None
        and row.stripe_subscription_id
        and subscription_id
        and row.stripe_subscription_id != subscription_id
    ):
        logger.info(
            "Ignoring deletion of a superseded subscription: event=%s stored=%s",
            subscription_id,
            row.stripe_subscription_id,
        )
        return

    await _downgrade_to_free(
        db, str(user_id), stripe_status="canceled", event_created=event_created
    )


@router.get("/health", status_code=status.HTTP_200_OK)
async def webhook_health() -> dict:
    """Health check endpoint for webhook consumers."""
    return {"status": "healthy"}
