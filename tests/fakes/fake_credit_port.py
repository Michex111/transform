from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class FakeCreditPort:
    """Configurable stand-in for the worker's CreditPort.

    Tracks consume calls (including the origin they were made with) and supports
    simulating transient failures for the credit pre-check and the
    post-conversion deduction.

    ``remaining`` is the *plan* bucket, while ``available_total`` is the
    wallet-wide spendable number the pre-check gates on. They default to the
    same value; a test that wants "zero plan credits but purchased credits
    left" sets ``remaining=0, available_total=5``.
    """

    def __init__(
        self,
        remaining: int | None = 100,
        tier: SubscriptionTier = SubscriptionTier.FREE,
        available_total: int | None = None,
    ) -> None:
        self.remaining = remaining
        self.tier = tier
        self.available_total = remaining if available_total is None else available_total
        self.consume_calls: list[tuple[int, str, int]] = []
        self.consume_origins: list[JobOrigin] = []
        self.fail_get_remaining = False
        self.fail_get_tier = False
        self.fail_consume = False

    async def get_remaining(self, user_id: int, period_key: str) -> int | None:
        del user_id, period_key
        if self.fail_get_remaining:
            raise RuntimeError("get_remaining boom")
        return self.remaining

    async def get_available_total(self, user_id: int, period_key: str) -> int | None:
        del user_id, period_key
        # Shares the ``fail_get_remaining`` flag: the processor's pre-check now
        # reads the wallet-wide total, and a test simulating a credit-DB outage
        # should see the pre-check fail regardless of which accessor it calls.
        if self.fail_get_remaining:
            raise RuntimeError("get_available_total boom")
        return self.available_total

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        del user_id
        if self.fail_get_tier:
            raise RuntimeError("get_tier boom")
        return self.tier

    async def consume(
        self,
        user_id: int,
        period_key: str,
        units: int,
        *,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> int:
        if self.fail_consume:
            raise RuntimeError("consume boom")
        self.consume_calls.append((user_id, period_key, units))
        self.consume_origins.append(origin)
        if self.remaining is not None:
            self.remaining -= units
        # Clamp at 0 — a credit bucket never goes negative.
        self.remaining = max(self.remaining or 0, 0)
        return self.remaining
