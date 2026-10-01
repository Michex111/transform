"""SQLAlchemy repository for user subscriptions (tier + storage usage)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserSubscriptionModel


class SQLSubscriptionRepository:
    """Persists subscription tier and storage usage per actor."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        """Return the active tier for an actor, defaulting to FREE."""
        row = await self._session.get(UserSubscriptionModel, actor_key)
        if row is None:
            return SubscriptionTier.FREE
        return row.tier

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        """Return the tier for a user id, defaulting to FREE."""
        result = await self._session.execute(
            select(UserSubscriptionModel).where(UserSubscriptionModel.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return SubscriptionTier.FREE
        return row.tier

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
    ) -> None:
        """Create or update an actor's subscription row."""
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
