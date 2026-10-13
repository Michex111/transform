"""Read-side service for a user's credit balance.

WHY this exists as a service rather than living in the two places that need it:
``GET /api/v1/credits/balance`` and the MCP ``get_credits`` tool must report the
*same* number, derived the same way. The REST handler used to compute it inline,
which is fine until a second caller appears and the two drift. This module is
that one derivation.

The arithmetic itself is not re-implemented here — it composes the existing,
already-tested pieces:

* :class:`TierPolicy` for the plan allowance and whether credits reset at all,
* :func:`current_period_key` / :func:`next_period_start` so the bucket and the
  reset instant can never straddle a month boundary,
* :func:`available_total` (via :class:`WalletBalances`) so an expired carryover
  contributes zero at read time, exactly as it does everywhere else.

``balance`` is the plan bucket (the number the dashboard labels "remaining"),
while ``total_available`` adds live carryover and purchased credits. Both are
returned so a caller can show either without recomputing the rules.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.credit_wallet import WalletBalances, available_total
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import (
    current_period_key,
    ensure_utc,
    next_period_start,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class CreditBucketPort(Protocol):
    """Reads a user's monthly credit bucket."""

    async def get_credit(self, owner_id: str, period_key: str) -> Credit | None: ...


class CreditWalletPort(Protocol):
    """Reads the user's tier and the wallet columns on their subscription row.

    ``get_wallet`` returns the subscription row itself (a persistence model this
    layer deliberately does not import); the service only reads its wallet
    columns.
    """

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier: ...

    async def get_wallet(self, user_id: int) -> Any: ...



@dataclass(frozen=True)
class CreditBalance:
    """A user's credit position for the active period.

    ``balance`` is the plan bucket for the current month; ``total_available``
    also counts live carryover and purchased credits. ``resets_at`` is the first
    instant of the next period when plan credits reset, or ``None`` for a tier
    whose credits never reset (unlimited/enterprise-style allowances).
    """

    balance: int
    total_available: int
    resets_at: datetime | None


class CreditService:
    """Answers "how many credits does this user have right now?"."""

    def __init__(
        self,
        *,
        credit_repository: CreditBucketPort,
        subscription_repository: CreditWalletPort,
    ) -> None:
        self._credits = credit_repository
        self._subscriptions = subscription_repository

    async def get_balance(
        self, user_id: int, *, now: datetime | None = None
    ) -> CreditBalance:
        """Return the user's balance for the current period.

        ``now`` is injectable so a test can pin the period and the reset
        boundary; production passes nothing and uses the wall clock. One instant
        is resolved up front so the period key, the carryover-expiry check and
        the reset date all agree even across a month boundary.
        """
        tier = await self._subscriptions.get_tier_for_user(user_id)
        policy = TierPolicy.for_tier(tier)
        monthly_credits = policy.monthly_conversion_credits
        allowance = monthly_credits or 0

        instant = now or datetime.now(UTC)
        period_key = current_period_key(instant)
        credit = await self._credits.get_credit(str(user_id), period_key)
        remaining = credit.remaining if credit is not None else allowance

        wallet = await self._subscriptions.get_wallet(user_id)
        carryover = wallet.carryover_credits if wallet is not None else 0
        carryover_expires_at = ensure_utc(
            wallet.carryover_expires_at if wallet is not None else None
        )
        purchased = wallet.purchased_credits if wallet is not None else 0

        total = available_total(
            WalletBalances(
                plan_remaining=remaining,
                carryover_credits=carryover,
                purchased_credits=purchased,
            ),
            carryover_expires_at=carryover_expires_at,
            now=instant,
        )
        return CreditBalance(
            balance=remaining,
            total_available=total,
            resets_at=next_period_start(instant) if monthly_credits is not None else None,
        )
