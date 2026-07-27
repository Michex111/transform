from dataclasses import dataclass

from src.domain.subscriptions.exceptions import (
    GuestTierHasNoPersistentCredits,
    InsufficientCredits,
    InvalidCreditOperation,
)
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.tier import SubscriptionTier

@dataclass
class Credit:
    """Represents a monthly credit bucket for one user.

    Attributes:
        owner_id: User identifier that owns this credit bucket.
        period_key: Accounting period (for example: '2026-07').
        allowance: Total credits granted for the period.
        remaining: Credits currently available.
    """

    owner_id: str
    period_key: str
    allowance: int
    remaining: int

    def __post_init__(self):
        """Validates the credit bucket after initialization."""
        if self.allowance < 0:
            raise ValueError("credit allowance cannot be negative.")
        if self.remaining < 0:
            raise ValueError("Remaining credits cannot be negative.")
        if self.remaining > self.allowance:
            raise ValueError("Remaining credits cannot exceed allowance.")

    @classmethod
    def from_tier(cls, owner_id: str, period_key: str, tier: SubscriptionTier) -> 'Credit':
        """Creates a monthly credit bucket based on tier policy.

        Args:
            owner_id: User identifier.
            period_key: Accounting period key.
            tier: User subscription tier.

        Returns:
            A Credit object initialized at full monthly allowance.

        Raises:
            GuestTierHasNoPersistentCredits: If tier is guest.
        """
        policy: TierPolicy = TierPolicy.for_tier(tier)
        if policy.monthly_conversion_credits is None:
            raise GuestTierHasNoPersistentCredits(
                "Guest tier does not use persistent monthly credits."
            )

        return cls(
            owner_id=owner_id,
            period_key=period_key,
            allowance=policy.monthly_conversion_credits,
            remaining=policy.monthly_conversion_credits,
        )

    def consume_for_conversion(self, units: int = 1) -> None:
        """Deducts credits for a conversion operation.

        Args:
            units: Credits to consume.

        Raises:
            InvalidCreditOperation: If units is not a positive integer.
            InsufficientCredits: If remaining credits are insufficient.
        """
        if units <= 0:
            raise InvalidCreditOperation("Units to consume must be a positive integer.")
        if self.remaining < units:
            raise InsufficientCredits(
                f"Insufficient credits: {self.remaining} remaining, {units} requested."
            )
        self.remaining -= units

    def reset_to_monthly_allowance(self) -> None:
        """Resets the remaining credits to the monthly allowance."""
        self.remaining = self.allowance