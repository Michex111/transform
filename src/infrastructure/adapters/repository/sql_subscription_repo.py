"""SQLAlchemy repository for user subscriptions (tier + lifecycle + wallet)."""

from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.policies.payment_grace import effective_tier
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    ensure_utc,
)
from src.domain.subscriptions.value_object.payment_state import PaymentState
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import MonthlyCreditModel, UserSubscriptionModel


def _effective(row: UserSubscriptionModel, now: datetime) -> SubscriptionTier:
    """Derive the enforced tier for a loaded row.

    The single place a stored row becomes an entitlement decision. Every caller
    — API request, worker, MCP, assistant — inherits the grace rule from here,
    so a payment failure cannot leave paid access alive in one subsystem while
    another revokes it.

    Reading the deadline on every call (rather than trusting a background job to
    have flipped ``tier``) is what makes the three-day limit a *maximum*: the
    sweep can be late, or the whole worker can be down, and the customer's
    access still ends on time.
    """
    return effective_tier(
        tier=row.tier,
        payment_state=row.payment_state,
        grace_deadline_at=ensure_utc(row.grace_deadline_at),
        stripe_status=row.stripe_status,
        now=now,
    )


class SQLSubscriptionRepository:
    """Persists subscription tier, lifecycle state and storage usage per actor."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        """Return the enforced tier for an actor, defaulting to FREE."""
        row = await self._session.get(UserSubscriptionModel, actor_key)
        if row is None:
            return SubscriptionTier.FREE
        return _effective(row, datetime.now(UTC))

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        """Return the **enforced** tier for a user id, defaulting to FREE.

        This deliberately does not return ``row.tier`` verbatim any more. The
        stored tier is the plan the customer bought; what they may use is the
        derived value, which drops to FREE once a grace window closes.
        """
        result = await self._session.execute(
            select(UserSubscriptionModel).where(UserSubscriptionModel.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return SubscriptionTier.FREE
        return _effective(row, datetime.now(UTC))

    async def get_by_stripe_subscription_id(
        self, stripe_subscription_id: str
    ) -> UserSubscriptionModel | None:
        """Resolve a local row from a Stripe subscription id.

        Matching on the subscription id (not only on metadata) is what stops a
        late event for an *old* subscription from overwriting the state of a
        current one.
        """
        result = await self._session.execute(
            select(UserSubscriptionModel).where(
                UserSubscriptionModel.stripe_subscription_id == stripe_subscription_id
            )
        )
        return result.scalars().first()

    async def get_by_stripe_customer_id(
        self, stripe_customer_id: str
    ) -> UserSubscriptionModel | None:
        """Resolve a local row from a Stripe customer id."""
        result = await self._session.execute(
            select(UserSubscriptionModel).where(
                UserSubscriptionModel.stripe_customer_id == stripe_customer_id
            )
        )
        return result.scalars().first()

    # --- Lifecycle transitions --------------------------------------------
    # Every transition is a single guarded UPDATE. The guard is the idempotency
    # mechanism: a transition that has already happened matches zero rows, so
    # duplicate deliveries, replays and concurrent workers cannot move state
    # twice or extend a deadline.

    async def open_grace(
        self,
        *,
        user_id: int,
        anchor: datetime,
        deadline: datetime,
        invoice_id: str | None,
        event_created: datetime | None,
        stripe_status: str | None = None,
    ) -> bool:
        """Enter the grace state for a user, if they are not already in it.

        Returns True when this call performed the ``OK -> GRACE`` transition.
        Once a row is in grace its deadline is **never** recomputed, which is
        what stops repeated failures, retries and webhook replays from pushing
        the window out indefinitely.
        """
        anchor = ensure_utc(anchor) or anchor
        values: dict[str, object] = {
            "payment_state": PaymentState.GRACE.value,
            "grace_started_at": anchor,
            "grace_deadline_at": deadline,
            "grace_source_invoice_id": invoice_id,
            "last_payment_failed_at": anchor,
            "updated_at": datetime.now(UTC),
        }
        if stripe_status is not None:
            values["stripe_status"] = stripe_status
        if event_created is not None:
            values["last_payment_event_at"] = event_created

        conditions = [
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.payment_state == PaymentState.OK.value,
        ]
        if event_created is not None:
            # Ordering guard: an event older than the newest one applied must
            # not open a window.
            conditions.append(
                or_(
                    UserSubscriptionModel.last_payment_event_at.is_(None),
                    UserSubscriptionModel.last_payment_event_at <= event_created,
                )
            )

        result = await self._session.execute(
            update(UserSubscriptionModel)
            .where(*conditions)
            .values(**values)
            # ``synchronize_session=False``: the guard compares a DB timestamp
            # against an event timestamp, and SQLite returns naive datetimes
            # while the parameter is aware. Letting SQLAlchemy evaluate that
            # comparison in Python raises a TypeError; the server-side WHERE is
            # the authority anyway.
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def touch_payment_failure(
        self,
        *,
        user_id: int,
        failed_at: datetime,
        event_created: datetime | None,
        stripe_status: str | None = None,
    ) -> None:
        """Record an additional failure **without** touching the deadline.

        Used for the second, third and Nth failed invoice of the same dunning
        episode: the customer should be able to see the latest attempt, but the
        window opened by the first failure must not move.
        """
        values: dict[str, object] = {
            "last_payment_failed_at": failed_at,
            "updated_at": datetime.now(UTC),
        }
        if stripe_status is not None:
            values["stripe_status"] = stripe_status
        if event_created is not None:
            values["last_payment_event_at"] = event_created

        await self._session.execute(
            update(UserSubscriptionModel)
            .where(UserSubscriptionModel.user_id == user_id)
            .values(**values)
        )
        await self._session.commit()

    async def clear_grace(
        self,
        *,
        user_id: int,
        event_created: datetime | None,
        stripe_status: str | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> bool:
        """End a grace window after a confirmed payment.

        Idempotent and order-guarded: a stale success that arrives after a newer
        failure must not resurrect entitlement. The deadlin columns are cleared
        so a future failure opens a fresh window from scratch.
        """
        values: dict[str, object] = {
            "payment_state": PaymentState.OK.value,
            "grace_started_at": None,
            "grace_deadline_at": None,
            "grace_source_invoice_id": None,
            "entitlement_revoked_at": None,
            "previous_paid_tier": None,
            "updated_at": datetime.now(UTC),
        }
        if stripe_status is not None:
            values["stripe_status"] = stripe_status
        if current_period_end is not None:
            values["current_period_end"] = current_period_end
        if cancel_at_period_end is not None:
            values["cancel_at_period_end"] = cancel_at_period_end
        if event_created is not None:
            values["last_payment_event_at"] = event_created

        conditions = [
            UserSubscriptionModel.user_id == user_id,
            UserSubscriptionModel.payment_state.in_(
                [PaymentState.GRACE.value, PaymentState.REVOKED.value]
            ),
        ]
        if event_created is not None:
            conditions.append(
                or_(
                    UserSubscriptionModel.last_payment_event_at.is_(None),
                    UserSubscriptionModel.last_payment_event_at <= event_created,
                )
            )

        result = await self._session.execute(
            update(UserSubscriptionModel)
            .where(*conditions)
            .values(**values)
            # See :meth:`open_grace` for why session synchronisation is off.
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return bool(getattr(result, "rowcount", 0))

    async def mirror_stripe_state(
        self,
        user_id: int,
        *,
        stripe_status: str | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> None:
        """Persist what Stripe reported, without changing entitlement.

        Kept separate from the transition methods so a status mirror can never
        accidentally grant or revoke access; only the explicit transitions do
        that.
        """
        values: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if stripe_status is not None:
            values["stripe_status"] = stripe_status
        if current_period_end is not None:
            values["current_period_end"] = current_period_end
        if cancel_at_period_end is not None:
            values["cancel_at_period_end"] = cancel_at_period_end

        await self._session.execute(
            update(UserSubscriptionModel)
            .where(UserSubscriptionModel.user_id == user_id)
            .values(**values)
        )
        await self._session.commit()

    async def get_due_grace_rows(
        self, *, now: datetime, limit: int = 200
    ) -> list[UserSubscriptionModel]:
        """Return rows whose grace window has closed, claiming them for update.

        ``FOR UPDATE SKIP LOCKED`` lets two sweepers run without blocking or
        duplicating work, and the ``payment_state`` predicate keeps the scan on
        the partial index created by migration 0029.
        """
        result = await self._session.execute(
            select(UserSubscriptionModel)
            .where(
                UserSubscriptionModel.payment_state == PaymentState.GRACE.value,
                UserSubscriptionModel.grace_deadline_at.is_not(None),
                UserSubscriptionModel.grace_deadline_at <= now,
            )
            .order_by(UserSubscriptionModel.grace_deadline_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def revoke_entitlement(self, row: UserSubscriptionModel, *, now: datetime) -> None:
        """Remove paid entitlement from a loaded row, without cancelling Stripe.

        The plan is remembered in ``previous_paid_tier`` so a later successful
        payment restores exactly what was taken away. ``purchased_credits`` is
        untouched: those were bought outright and never expire.
        """
        if row.tier not in (SubscriptionTier.FREE, SubscriptionTier.GUEST):
            row.previous_paid_tier = str(row.tier.value)
        row.tier = SubscriptionTier.FREE
        row.payment_state = PaymentState.REVOKED.value
        row.entitlement_revoked_at = now
        row.updated_at = now

        # The plan bucket is part of the paid entitlement: without this the
        # customer keeps the plan's remaining monthly conversions even after the
        # tier drops. Purchased and carryover credits are deliberately left
        # alone — one was paid for, the other has its own expiry date.
        free_grant = TierPolicy.for_tier(SubscriptionTier.FREE).monthly_conversion_credits or 0
        if row.user_id is not None:
            credit_result = await self._session.execute(
                select(MonthlyCreditModel).where(
                    MonthlyCreditModel.owner_id == str(row.user_id),
                    MonthlyCreditModel.period_key == current_period_key(now),
                )
            )
            credit_row = credit_result.scalar_one_or_none()
            if credit_row is not None:
                credit_row.allowance = min(credit_row.allowance, free_grant)
                credit_row.remaining = min(credit_row.remaining, free_grant)
                credit_row.updated_at = now

    async def commit(self) -> None:
        """Commit the shared unit of work."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Discard the shared unit of work."""
        await self._session.rollback()

    async def get_used_storage_bytes(self, actor_key: str) -> int:
        """Return storage currently consumed by the actor."""
        row = await self._session.get(UserSubscriptionModel, actor_key)
        if row is None:
            return 0
        return row.used_storage_bytes or 0

    async def set_used_storage_bytes(self, actor_key: str, used_storage_bytes: int) -> None:
        """Persist the actor's current storage usage."""
        row = await self._session.get(UserSubscriptionModel, actor_key)
        now = datetime.now(UTC)
        if row is None:
            self._session.add(
                UserSubscriptionModel(
                    actor_key=actor_key,
                    tier=SubscriptionTier.FREE,
                    used_storage_bytes=used_storage_bytes,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.used_storage_bytes = used_storage_bytes
            row.updated_at = now
        await self._session.commit()

    async def upsert_subscription(
        self,
        *,
        actor_key: str,
        user_id: int | None,
        tier: SubscriptionTier,
        used_storage_bytes: int | None = None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        stripe_status: str | None = None,
    ) -> None:
        """Create or update an actor's subscription row.

        ``payment_state`` is deliberately NOT written here: activation must not
        silently clear a grace window. Callers that have confirmed a payment use
        :meth:`clear_grace` for that, which carries the ordering guard.
        """
        row = await self._session.get(UserSubscriptionModel, actor_key)
        now = datetime.now(UTC)
        if row is None:
            self._session.add(
                UserSubscriptionModel(
                    actor_key=actor_key,
                    user_id=user_id,
                    tier=tier,
                    used_storage_bytes=used_storage_bytes or 0,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    stripe_status=stripe_status,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.tier = tier
            if user_id is not None:
                row.user_id = user_id
            if used_storage_bytes is not None:
                row.used_storage_bytes = used_storage_bytes
            if stripe_customer_id is not None:
                row.stripe_customer_id = stripe_customer_id
            if stripe_subscription_id is not None:
                row.stripe_subscription_id = stripe_subscription_id
            if stripe_status is not None:
                row.stripe_status = stripe_status
            row.updated_at = now
        await self._session.commit()

    async def get_subscription_row(self, user_id: int) -> UserSubscriptionModel | None:
        """Fetch the subscription row for a user id."""
        result = await self._session.execute(
            select(UserSubscriptionModel).where(UserSubscriptionModel.user_id == user_id)
        )
        return result.scalar_one_or_none()

    # --- Credit wallet -----------------------------------------------------
    # ``user_subscriptions`` owns two of the three credit populations
    # (``carryover_credits`` and ``purchased_credits``); the plan population
    # lives in ``monthly_credits`` and is read/written through
    # ``SQLCreditRepository``. These helpers keep wallet access on the
    # repository that owns the columns rather than having callers poke ORM
    # attributes directly.

    async def get_wallet(self, user_id: int) -> UserSubscriptionModel | None:
        """Return the row carrying the wallet pools, or None for a user with no
        subscription row (their wallet is empty, not an error)."""
        return await self.get_subscription_row(user_id)

    def apply_wallet_balances(
        self,
        row: UserSubscriptionModel,
        *,
        carryover_credits: int,
        purchased_credits: int,
    ) -> None:
        """Write consumed wallet balances onto a *loaded* row, without committing.

        Deliberately does not commit: the worker calls this in the same session
        that then writes the plan bucket (``monthly_credits``) via
        ``SQLCreditRepository.save_credit``, whose commit flushes both rows as a
        single transaction. Committing here instead would split the wallet in
        two, so a failed plan write could leave carryover spent but plan intact.
        """
        row.carryover_credits = carryover_credits
        row.purchased_credits = purchased_credits
        row.updated_at = datetime.now(UTC)

    async def set_credit_preference(
        self, user_id: int, purchased_credits_first: bool
    ) -> None:
        """Persist the credit spend-order preference for a user.

        A single-column write rather than a read-modify-write of the whole
        wallet. That matters for more than tidiness: routing this through
        :meth:`set_wallet` would mean the request carrying carryover and
        purchased balances back from the client, i.e. letting a client rewrite
        its own balances.

        Creates a FREE row when the user has none. Someone on the free tier can
        still buy credit packs, so "no subscription row yet" is not a reason to
        refuse a preference — and the row is what the purchased-credit grant
        needs to exist anyway. Mirrors :meth:`set_used_storage_bytes`.
        """
        result = await self._session.execute(
            select(UserSubscriptionModel).where(UserSubscriptionModel.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            self._session.add(
                UserSubscriptionModel(
                    actor_key=f"user:{user_id}",
                    user_id=user_id,
                    tier=SubscriptionTier.FREE,
                    used_storage_bytes=0,
                    purchased_credits_first=purchased_credits_first,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.purchased_credits_first = purchased_credits_first
            row.updated_at = now
        await self._session.commit()

    async def set_wallet(
        self,
        user_id: int,
        *,
        carryover_credits: int,
        carryover_expires_at: datetime | None,
        purchased_credits: int,
        purchased_credits_first: bool,
        commit: bool = True,
    ) -> UserSubscriptionModel | None:
        """Persist the full wallet state for a user and (by default) commit.

        Takes every field explicitly rather than defaulting some to "leave
        unchanged": the upgrade path must be able to *clear* an expiry as well
        as set one, and a sentinel/None-means-unchanged convention would make
        ``carryover_expires_at=None`` (a legitimate value) unexpressible.
        Returns the updated row, or ``None`` when the user has no row to update.

        ``commit=False`` leaves the change pending so a caller that also writes
        ``monthly_credits`` can commit both in one transaction. The default
        preserves the historical behaviour.
        """
        row = await self.get_subscription_row(user_id)
        if row is None:
            return None
        row.carryover_credits = carryover_credits
        row.carryover_expires_at = carryover_expires_at
        row.purchased_credits = purchased_credits
        row.purchased_credits_first = purchased_credits_first
        row.updated_at = datetime.now(UTC)
        if commit:
            await self._session.commit()
        return row
