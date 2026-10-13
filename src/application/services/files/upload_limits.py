"""Per-file upload caps by subscription tier.

Extracted from ``FileService`` so the upload path and the dashboard answer
"how big may one file be for this tier?" from exactly one place. ``FileService``
enforces it; ``/user/dashboard`` reports it so the SPA never has to hardcode a
limit that the server might have changed.

Note the deliberate distinction between this cap and
``TierPolicy.storage_quota_bytes`` (the ACCOUNT quota). They answer different
questions and are configured separately — see the upload limits section of
``infrastructure/config/settings.py``.
"""

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings


def default_size_limits() -> dict[SubscriptionTier, int]:
    """Tier → maximum size of a SINGLE file, in bytes."""
    settings = get_settings()
    return {
        SubscriptionTier.GUEST: settings.GUEST_MAX_FILE_SIZE,
        SubscriptionTier.FREE: settings.FREE_MAX_FILE_SIZE,
        SubscriptionTier.PREMIUM: settings.PRO_MAX_FILE_SIZE,
        SubscriptionTier.PRO: settings.PRO_MAX_FILE_SIZE,
        SubscriptionTier.PRO_PLUS: settings.PRO_PLUS_MAX_FILE_SIZE,
        SubscriptionTier.ENTERPRISE: settings.ENTERPRISE_MAX_FILE_SIZE,
    }


def tier_max_file_size_bytes(
    tier: SubscriptionTier,
    size_limits: dict[SubscriptionTier, int] | None = None,
) -> int:
    """The per-file cap for ``tier``, honouring an explicit override map."""
    limits = size_limits if size_limits is not None else default_size_limits()
    return limits[tier]
