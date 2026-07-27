from dataclasses import dataclass

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.exceptions import (
    GuestTierRequiresRateLimiting,
    InvalidSubscriptionState,
    MissingCreditAccount,
    StorageQuotaExceeded,
)
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.tier import SubscriptionTier

@dataclass
class Subscription:
    """
    Represents tier and storage state for one actor.

    Attributes:
        tier: Active subscription tier.
        used_storage_bytes: Current storage consumed in bytes.

    """
    tier: SubscriptionTier
    used_storage_bytes: int = 0

    def __post_init__(self):
        """Validates the subscription after initialization."""
        if self.used_storage_bytes < 0:
            raise ValueError("used_storage_bytes cannot be negative.")

    @property
    def policy(self) -> TierPolicy:
        """Returns the policy derived from current tier."""
        return TierPolicy.for_tier(self.tier)

    @property
    def storage_quota_bytes(self) -> int:
        """Returns storage quota in bytes for this subscription."""
        return self.policy.storage_quota_bytes

    def ensure_storage_available(self, additional_bytes: int) -> None:
        """Validates whether more data can be stored.

        Args:
            additional_bytes: Additional bytes to allocate.

        Raises:
            InvalidSubscriptionState: If additional bytes is negative.
            StorageQuotaExceeded: If resulting usage exceeds quota.
        """
        if additional_bytes < 0:
            raise InvalidSubscriptionState("additional_bytes cannot be negative.")

        projected = self.used_storage_bytes + additional_bytes
        if projected > self.storage_quota_bytes:
            raise StorageQuotaExceeded(
                f"Storage quota exceeded: projected={projected}, "
                f"quota={self.storage_quota_bytes}."
            )

    def allocate_storage(self, additional_bytes: int) -> None:
        """
        Allocates storage after quota validation.

        Args:
            additional_bytes: Bytes to add to usage.
        """
        self.ensure_storage_available(additional_bytes)
        self.used_storage_bytes += additional_bytes

    def release_storage(self, bytes_to_release: int) -> None:
        """Releases storage already in use.

        Args:
            bytes_to_release: Bytes to remove from usage.

        Raises:
            InvalidSubscriptionState: If released bytes are invalid.
        """
        if bytes_to_release < 0:
            raise InvalidSubscriptionState("bytes_to_release cannot be negative.")
        if bytes_to_release > self.used_storage_bytes:
            raise InvalidSubscriptionState(
                f"Cannot release more storage than used: "
                f"used={self.used_storage_bytes}, requested={bytes_to_release}."
            )
        self.used_storage_bytes -= bytes_to_release

    def consume_conversion_credits(self, credit: Credit | None) -> None:
        """
        Consumes one conversion credit before conversion is allowed.

        Args:
            credit: Persistent credit bucket for authenticated users.

        Raises:
            GuestTierRequiresRateLimiting: For guest tier.
            MissingCreditAccount: If persistent tier has no credit account.
        """
        if self.tier == SubscriptionTier.GUEST:
            raise GuestTierRequiresRateLimiting(
                "Guest conversion must pass through IP-based rate limiting."
            )
        if credit is None:
            raise MissingCreditAccount(
                f"Tier {self.tier} requires a persistent credit account."
            )
        credit.consume_for_conversion(1)

    