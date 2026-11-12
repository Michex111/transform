"""Dependency wiring for the billing worker."""

from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from workers.billing_worker.worker import BillingWorker


def build_billing_worker() -> BillingWorker:
    """Construct a configured :class:`BillingWorker` from environment settings."""
    settings = get_settings()
    return BillingWorker(
        get_session_factory(),
        sweep_interval_seconds=settings.PAYMENT_GRACE_SWEEP_INTERVAL_SECONDS,
    )
