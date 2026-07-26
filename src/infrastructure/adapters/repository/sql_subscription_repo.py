from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import MonthlyCreditModel, UserSubscriptionModel


class SQLSubscriptionRepository:
    """SQL adapter for subscription state and monthly credits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        """Returns the actor tier from storage or falls back to guest."""
        stmt = select(UserSubscriptionModel).where(UserSubscriptionModel.actor_key == actor_key)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            if actor_key.startswith("user:") or actor_key.startswith("sdk:"):
                return SubscriptionTier.FREE
            return SubscriptionTier.GUEST
        return row.tier

    async def get_used_storage_bytes(self, actor_key: str) -> int:
        """Returns tracked bytes in use for an actor key."""
        stmt = select(UserSubscriptionModel).where(UserSubscriptionModel.actor_key == actor_key)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return 0
        return row.used_storage_bytes

    async def set_used_storage_bytes(self, actor_key: str, used_storage_bytes: int) -> None:
        """Sets tracked bytes in use for an actor key."""
        stmt = select(UserSubscriptionModel).where(UserSubscriptionModel.actor_key == actor_key)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            default_tier = SubscriptionTier.GUEST
            if actor_key.startswith("user:") or actor_key.startswith("sdk:"):
                default_tier = SubscriptionTier.FREE
            row = UserSubscriptionModel(
                actor_key=actor_key,
                tier=default_tier,
                used_storage_bytes=used_storage_bytes,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.used_storage_bytes = used_storage_bytes
            row.updated_at = now
        await self._session.commit()

    async def get_credit(self, owner_id: str, period_key: str) -> Credit | None:
        """Returns monthly credit ledger for one user and period."""
        stmt = select(MonthlyCreditModel).where(
            MonthlyCreditModel.owner_id == owner_id,
            MonthlyCreditModel.period_key == period_key,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return Credit(
            owner_id=row.owner_id,
            period_key=row.period_key,
            allowance=row.allowance,
            remaining=row.remaining,
        )

    async def save_credit(self, credit: Credit) -> None:
        """Persists monthly credit ledger values."""
        stmt = select(MonthlyCreditModel).where(
            MonthlyCreditModel.owner_id == credit.owner_id,
            MonthlyCreditModel.period_key == credit.period_key,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            row = MonthlyCreditModel(
                owner_id=credit.owner_id,
                period_key=credit.period_key,
                allowance=credit.allowance,
                remaining=credit.remaining,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.allowance = credit.allowance
            row.remaining = credit.remaining
            row.updated_at = now
        await self._session.commit()
