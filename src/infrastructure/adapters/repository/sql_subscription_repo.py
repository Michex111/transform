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
