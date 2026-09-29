"""SQLAlchemy adapter behind the assistant's read-only account port.

WHY it mirrors ``routers/v1/dashboard.py`` instead of calling it: the dashboard
endpoint is an HTTP handler (it takes a FastAPI ``CurrentUser`` and returns a
response model), so the assistant cannot reuse it directly. Duplicating the
*arithmetic* — not the presentation — in one small adapter is the price of not
leaking the dashboard's transport types into the agent loop; every number here
is derived from the same repository calls, the same ``TierPolicy`` and the same
``credit_period`` helpers the dashboard uses, so the two can never disagree.

The adapter is read-only by construction: it holds the four repositories but
only ever calls their query methods, so no tool built on it can mutate credits,
storage or jobs.
"""

from datetime import UTC, datetime

from src.application.ports.assistant_account_port import AccountOverview
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    next_period_start,
)
from src.infrastructure.adapters.repository.sql_conversion_job_repo import (
    SQLConversionJobRepository,
)
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import (
    SQLSubscriptionRepository,
)
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository


class SQLAssistantAccountAdapter:
    """Aggregates credits, storage and job counts for one user.

    Constructed per request with repositories bound to the request's single
    ``AsyncSession`` (matching every other SQL adapter), so all reads share one
    transaction and no extra pooled connection is checked out.
    """

    def __init__(
        self,
        *,
        job_repository: SQLConversionJobRepository,
        credit_repository: SQLCreditRepository,
        subscription_repository: SQLSubscriptionRepository,
        file_repository: SQLUserFileRepository,
    ) -> None:
        self._jobs = job_repository
        self._credits = credit_repository
        self._subscriptions = subscription_repository
        self._files = file_repository

    async def overview(
        self,
        user_id: int,
        *,
        since: datetime | None,
        fmt: str | None,
        status: str | None,
    ) -> AccountOverview:
        """Build the snapshot the assistant's account tool reports.

        The credit balance follows the dashboard's fallback exactly: a
        persisted bucket for the current period wins, otherwise the tier
        allowance (and ``0`` for tiers that have no monthly allowance at all).
        """
        tier = await self._subscriptions.get_tier_for_user(user_id)
        policy = TierPolicy.for_tier(tier)

        # One instant per call, so the period key and the reset date cannot
        # straddle a month boundary (same rule as the dashboard).
        now = datetime.now(UTC)
        allowance = policy.monthly_conversion_credits
        credits_reset_at = next_period_start(now) if allowance is not None else None
        period_key = current_period_key(now)

        counts = await self._jobs.counts_by_status(
            user_id, since=since, fmt=fmt, status=status
        )
        used_bytes = await self._files.get_user_storage_used(user_id)
        credit = await self._credits.get_credit(str(user_id), period_key)
        by_target_format = await self._jobs.count_jobs_by_target_format(
            user_id, since=since, fmt=fmt, status=status
        )

        balance = credit.remaining if credit is not None else (allowance or 0)
        total = counts["TOTAL"]
        completed = counts["COMPLETED"]
        failed = counts["FAILED"]

        return AccountOverview(
            tier=str(tier),
            credits_remaining=balance,
            credits_reset_at=credits_reset_at,
            storage_used_bytes=used_bytes,
            storage_limit_bytes=policy.storage_quota_bytes,
            jobs_total=total,
            jobs_completed=completed,
            jobs_failed=failed,
            # Anything that is neither completed nor failed is still in flight
            # (PENDING/PROCESSING/AWAITING_UPLOAD); clamping at zero keeps a
            # legacy row with an unknown status from reporting a negative count.
            jobs_active=max(0, total - completed - failed),
            by_target_format=tuple(by_target_format),
        )
