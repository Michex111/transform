"""Billing worker — enforces the payment grace deadline in stored state.

Why a separate worker
---------------------
Entitlement is enforced **on read** (``SQLSubscriptionRepository.get_tier_for_user``
derives it from ``grace_deadline_at``), so a stopped worker can never *extend* a
grace window. This worker exists for the other half:

* **Convergence** — materialise the revocation so ``user_subscriptions.tier``,
  the billing UI, the credit wallet and any direct reader of the row all agree.
* **Audit** — record ``entitlement_revoked_at`` and ``previous_paid_tier``.
* **Credit hygiene** — clamp the current period's plan bucket to the FREE grant,
  so a revoked account does not keep spending the paid plan's remaining
  conversions (purchased credits are deliberately untouched).

It does **not** cancel the Stripe subscription. Revoking entitlement and
cancelling billing are different operations; the subscription is left exactly as
Stripe reports it, so a later payment restores the plan.

Placement
---------
The cleanup worker was not reused: it runs every six hours (too slow for a
three-day maximum), it points at the *development* ``.env`` in both compose
files, and its cycle can block behind an object-store listing. A dedicated loop
with its own interval keeps the enforcement cadence independent of both.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text

from src.infrastructure.adapters.repository.sql_subscription_repo import (
    SQLSubscriptionRepository,
)

logger = logging.getLogger(__name__)

#: Fixed advisory-lock key. A constant rather than ``hashtext(...)`` because
#: ``hashtext`` is not guaranteed stable across PostgreSQL major versions, and a
#: changed value would silently mean "no mutual exclusion at all".
SWEEP_ADVISORY_LOCK_KEY = 82_041_1001


class BillingWorker:
    """Periodic sweep that revokes entitlements whose grace window has closed."""

    def __init__(
        self,
        db_session_factory,
        *,
        sweep_interval_seconds: int = 300,
        batch_size: int = 200,
    ):
        self._db_factory = db_session_factory
        self._interval = sweep_interval_seconds
        self._batch_size = batch_size
        self._running = False
        # Woken by ``stop()`` so shutdown does not wait out a full interval.
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the periodic sweep loop."""
        self._running = True
        logger.info("Billing worker started, sweep interval=%ds", self._interval)

        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                logger.info("Billing worker cancelled")
                self._running = False
                return
            except Exception as exc:  # pragma: no cover - loop must survive errors
                logger.error("Grace sweep failed: %s", exc, exc_info=True)

            if not self._running:
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    def stop(self) -> None:
        """Stop after the current sweep."""
        self._running = False
        self._stop_event.set()
        logger.info("Billing worker stopped")

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def run_once(self) -> int:
        """Run one sweep, returning the number of entitlements revoked.

        The whole sweep is **one transaction**, which matters for two reasons:

        * The advisory lock is transaction-scoped, so committing per batch would
          release it and let a second instance start a parallel sweep.
        * A crash mid-sweep rolls back everything, so the next tick finds the
          same rows (the ``payment_state = 'GRACE'`` predicate is idempotent).

        The batch is capped so one tick cannot run for minutes behind a large
        backlog; the next tick continues where this one stopped.
        """
        async with self._db_factory() as session:
            # Transaction-scoped lock: the app runs through a pooler, where a
            # session-scoped lock is not reliably pinned to one backend
            # connection across statements.
            lock_result = await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": SWEEP_ADVISORY_LOCK_KEY},
            )
            if not lock_result.scalar():
                logger.info("Another billing sweep holds the lock; skipping this tick")
                return 0

            repo = SQLSubscriptionRepository(session)
            # Compare against the DATABASE clock so instances with skewed clocks
            # cannot revoke early or late.
            now_result = await session.execute(text("SELECT now()"))
            now = now_result.scalar() or datetime.now(UTC)

            due = await repo.get_due_grace_rows(now=now, limit=self._batch_size)
            revoked = len(due)
            for row in due:
                await repo.revoke_entitlement(row, now=now)
                logger.warning(
                    "Entitlement revoked after grace expiry: user=%s plan=%s deadline=%s",
                    row.user_id,
                    row.previous_paid_tier,
                    row.grace_deadline_at.isoformat() if row.grace_deadline_at else None,
                )
            await repo.commit()

        logger.info("Grace sweep finished: revoked=%d", revoked)
        return revoked
