from src.domain.subscriptions.value_object.tier import SubscriptionTier


class FakeCreditPort:
    """Configurable stand-in for the worker's CreditPort.

    Tracks consume calls and supports simulating transient failures for the
    credit pre-check and the post-conversion deduction.
    """

    def __init__(
        self,
        remaining: int | None = 100,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> None:
        self.remaining = remaining
        self.tier = tier
        self.consume_calls: list[tuple[int, str, int]] = []
        self.fail_get_remaining = False
        self.fail_get_tier = False
        self.fail_consume = False

    async def get_remaining(self, user_id: int, period_key: str) -> int | None:
        del user_id, period_key
        if self.fail_get_remaining:
            raise RuntimeError("get_remaining boom")
        return self.remaining

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        del user_id
        if self.fail_get_tier:
            raise RuntimeError("get_tier boom")
        return self.tier

    async def consume(self, user_id: int, period_key: str, units: int) -> int:
        if self.fail_consume:
            raise RuntimeError("consume boom")
        self.consume_calls.append((user_id, period_key, units))
        if self.remaining is not None:
            self.remaining -= units
        return self.remaining
