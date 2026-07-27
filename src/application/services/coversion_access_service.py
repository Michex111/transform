from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dtos.subscription_dto import ConversionActor, ConversionAuthorization
from src.application.exceptions.subscription_exceptions import (
    GuestRateLimitExceeded, 
    MissingActorIdentity
)
from src.application.ports.database_port import CreditRepository, SubscriptionRepository
from src.application.ports.contracts import RateLimiterPort
from src.application.services.queue_priority_router import QueuePriorityRouter
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.entities.subscription import Subscription
from src.domain.subscriptions.value_object.tier import SubscriptionTier

class ConversionAccessService:
    """Authorizes conversion requests against quota, credits, and guest rate limits."""

    def __init__(
        self,
        subscription_repository: SubscriptionRepository,
        credit_repository: CreditRepository,
        rate_limiter: RateLimiterPort,
        queue_router: QueuePriorityRouter,
        now_provider: Callable[[], datetime] | None = None,
        guest_conversion_limit: int = 10,
        guest_window_seconds: int = 60 * 60,
    ) -> None:
        self._subscription_repository = subscription_repository
        self._credit_repository = credit_repository
        self._rate_limiter = rate_limiter
        self._queue_router = queue_router
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._guest_conversion_limit = guest_conversion_limit
        self._guest_window_seconds = guest_window_seconds   

    async def authorize_conversion(self, actor: ConversionActor, incoming_file_size_bytes: int) -> ConversionAuthorization:
        """Validates a conversion request and consumes one credit when required.

        Args:
            actor: Requesting actor identity and tier.
            incoming_file_size_bytes: New file size that will be stored.

        Returns:
            ConversionAuthorization with resulting queue stream and credit state.

        Raises:
            GuestRateLimitExceeded: If guest actor is over limit.
            MissingActorIdentity: If a required user/ip identity is absent.
        """
        if incoming_file_size_bytes < 0:
            raise ValueError("Incoming file size cannot be negative.")

        used_storage_bytes = await self._subscription_repository.get_used_storage_bytes(actor.actor_key)
        subscription = Subscription(
            tier=SubscriptionTier(actor.tier),
            used_storage_bytes=used_storage_bytes,
        )
        subscription.ensure_storage_available(incoming_file_size_bytes)

        period_key = self._period_key()
        remaining_credits: int | None = None

        if actor.tier == SubscriptionTier.GUEST:
            if not actor.ip_address:
                raise MissingActorIdentity("Guest conversion requires actor.ip_address.")
            allowed = await self._rate_limiter.allow(
                key=actor.ip_address,
                limit=self._guest_conversion_limit,
                window_seconds=self._guest_window_seconds,
            )
            if not allowed:
                raise GuestRateLimitExceeded("Guest conversion rate limit exceeded.")
        else:
            if not actor.user_id:
                raise MissingActorIdentity("Authenticated conversion requires actor.user_id.")
            credit = await self._resolve_credit(actor.user_id, actor.tier, period_key)
            subscription.consume_conversion_credits(credit)
            await self._credit_repository.save_credit(credit)
            remaining_credits = credit.remaining

        return ConversionAuthorization(
            actor=actor,
            period_key=period_key,
            credits_remaining=remaining_credits,
            queue_stream=self._queue_router.stream_for_tier(actor.tier),
        )

    async def commit_storage_used(self, actor: ConversionActor, added_bytes: int) -> int:
        """Applies consumed storage after successful upload verification.

        Args:
            actor: Requesting actor identity and tier.
            added_bytes: Bytes to add.

        Returns:
            New used storage in bytes.
        """
        current = await self._subscription_repository.get_used_storage_bytes(actor.actor_key)
        subscription = Subscription(
            tier=SubscriptionTier(actor.tier),
            used_storage_bytes=current,
        )
        subscription.allocate_storage(added_bytes)
        await self._subscription_repository.set_used_storage_bytes(
            actor.actor_key, 
            subscription.used_storage_bytes
        )
        return subscription.used_storage_bytes

    async def _resolve_credit(self, user_id: str, tier: SubscriptionTier, period_key: str) -> Credit:
        """Returns existing monthly credit or creates a fresh period bucket."""
        credit = await self._credit_repository.get_credit(user_id, period_key)
        if credit is not None:
            return credit
        return Credit.from_tier(tier=tier, owner_id=user_id, period_key=period_key)

    def _period_key(self) -> str:
        """Builds the monthly period key used by credit ledgers."""
        return self._now_provider().strftime("%Y-%m")