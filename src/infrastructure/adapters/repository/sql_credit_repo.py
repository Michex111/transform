"""SQLAlchemy repository for monthly credit buckets and the credit ledger."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscriptions.entities.credit import Credit
from src.infrastructure.database.models import CreditTransactionModel, MonthlyCreditModel


class SQLCreditRepository:
    """Persists monthly credit buckets and credit transactions."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_credit(self, owner_id: str, period_key: str) -> Credit | None:
        """Return the credit bucket for a user and period, or None."""
        result = await self._session.execute(
            select(MonthlyCreditModel).where(
                MonthlyCreditModel.owner_id == owner_id,
                MonthlyCreditModel.period_key == period_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return Credit(
            owner_id=row.owner_id,
            period_key=row.period_key,
            allowance=row.allowance,
            remaining=row.remaining,
        )

    async def commit(self) -> None:
        """Commit the shared unit of work.

        The wallet write path spans two repositories built on the *same*
        request-scoped session — ``monthly_credits`` through this one and
        ``user_subscriptions`` through :class:`SQLSubscriptionRepository` — so
        committing here commits both. ``change-plan`` uses the ``commit=False``
        variants below and calls this once at the end, making a multi-write plan
        switch atomic instead of three independent commits.
        """
        await self._session.commit()

    async def rollback(self) -> None:
        """Discard the current unit of work (see :meth:`commit`)."""
        await self._session.rollback()

    async def save_credit(self, credit: Credit, *, commit: bool = True) -> None:
        """Insert or update a monthly credit bucket.

        ``commit=False`` leaves the change pending so a caller that mutates more
        than one table can commit them together. The default preserves the
        historical behaviour (every existing caller still commits).
        """
        result = await self._session.execute(
            select(MonthlyCreditModel).where(
                MonthlyCreditModel.owner_id == credit.owner_id,
                MonthlyCreditModel.period_key == credit.period_key,
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            self._session.add(
                MonthlyCreditModel(
                    owner_id=credit.owner_id,
                    period_key=credit.period_key,
                    allowance=credit.allowance,
                    remaining=credit.remaining,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.allowance = credit.allowance
            row.remaining = credit.remaining
            row.updated_at = now
        if commit:
            await self._session.commit()

    async def record_transaction(
        self,
        *,
        transaction_id: str,
        user_id: int,
        amount: int,
        transaction_type: str,
        reference_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> None:
        """Append a row to the credit transaction ledger.

        ``commit=False`` leaves the insert pending for a caller that commits a
        larger unit of work once (see :meth:`save_credit`).
        """
        self._session.add(
            CreditTransactionModel(
                id=transaction_id,
                user_id=user_id,
                amount=amount,
                transaction_type=transaction_type,
                reference_id=reference_id,
                description=description,
                created_at=datetime.now(UTC),
            )
        )
        if commit:
            await self._session.commit()

    async def list_transactions(
        self, user_id: int, offset: int = 0, limit: int = 20
    ) -> list[CreditTransactionModel]:
        """Return the user's credit transactions, newest first."""
        result = await self._session.execute(
            select(CreditTransactionModel)
            .where(CreditTransactionModel.user_id == user_id)
            .order_by(CreditTransactionModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())
