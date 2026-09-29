"""Redis-backed hourly quota for assistant turns.

WHY fixed hour buckets and not a sliding window: the limit exists to cap cost
and abuse over time, not to be a precise fairness guarantee, and a bucket key is
one ``INCR`` on one key — atomic by construction, with nothing to race. A
sliding window would need a sorted set, a trim and a ``ZCOUNT`` per turn for a
number no user will ever notice.

**Fail-open is deliberate.** Redis being unreachable must not turn the assistant
into a 500: the worst case of allowing a turn is a little unbilled compute,
while failing closed makes an unrelated incident look like the feature is
broken. The failure is logged as a warning (with the user id) so the quota gap
is visible in the logs, and the counter is still honest once Redis is back.
"""

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from src.domain.assistant.exceptions.assistant_exceptions import AssistantQuotaExceeded

logger = logging.getLogger(__name__)

#: Keys live past the end of their hour so a request arriving in the last
#: second of a bucket cannot race the expiry and reset the counter early.
_WINDOW_TTL_SECONDS = 2 * 60 * 60


class RedisAssistantQuota:
    """Counts assistant turns per user per hour."""

    def __init__(self, redis_client: Redis, prefix: str = "ai_quota:") -> None:
        self._client = redis_client
        self._prefix = prefix

    @staticmethod
    def _bucket(now: datetime | None = None) -> str:
        """The current hour bucket, as ``YYYYMMDDHH`` in UTC."""
        return (now or datetime.now(UTC)).strftime("%Y%m%d%H")

    def format_key(self, user_id: int) -> str:
        """The counter key for the current bucket."""
        return f"{self._prefix}{user_id}:{self._bucket()}"

    async def consume(self, user_id: int, limit: int) -> None:
        """Record one turn, raising when ``limit`` is already reached."""
        if limit <= 0:
            # No allowance at all: refuse without touching Redis, so a tier with
            # no access cannot be granted access by a Redis outage.
            raise AssistantQuotaExceeded(
                "The AI assistant is not available on your current plan."
            )

        key = self.format_key(user_id)
        try:
            count = await self._client.incr(key)
            if count == 1:
                await self._client.expire(key, _WINDOW_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            logger.warning(
                "Assistant quota check failed for user %s; allowing the request: %s",
                user_id,
                exc,
            )
            return

        if count > limit:
            raise AssistantQuotaExceeded(
                f"You have reached the hourly limit of {limit} assistant requests. "
                "Please try again later."
            )

    async def peek(self, user_id: int) -> int:
        """Turns already used in the current bucket, without recording one.

        A plain ``GET``: it neither increments nor creates the key, so merely
        opening the status endpoint cannot consume or "start" a user's hour.
        Fail-open like ``consume`` — an unreachable Redis reports 0 usage rather
        than failing the status request — and a non-numeric value (a key left by
        an incompatible release) is read as 0 instead of raising in the caller.
        """
        try:
            raw = await self._client.get(self.format_key(user_id))
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            logger.warning(
                "Assistant quota peek failed for user %s; reporting zero usage: %s",
                user_id,
                exc,
            )
            return 0
        if raw is None:
            # No bucket yet: the user has not made a turn this hour. A plain
            # GET (rather than INCR) is what keeps this from creating the key.
            return 0
        text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
        try:
            return int(text)
        except ValueError:
            return 0
