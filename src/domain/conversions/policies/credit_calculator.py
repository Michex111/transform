"""
Credit calculation service based on worker compute time.

Credits are proportional to the actual compute seconds consumed by the worker,
adjusted by a format-specific complexity multiplier and the user's tier discount.

Formula:
    raw_credits = ceil(compute_seconds * base_rate_per_second * format_multiplier)
    final_credits = ceil(raw_credits * tier_discount_factor)
"""

import math

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings


# Category → multiplier setting name mapping
_FORMAT_CATEGORY_MAP: dict[str, str] = {
    # Documents
    "pdf": "CREDIT_MULTIPLIER_DOCUMENT",
    "docx": "CREDIT_MULTIPLIER_DOCUMENT",
    "doc": "CREDIT_MULTIPLIER_DOCUMENT",
    "odt": "CREDIT_MULTIPLIER_DOCUMENT",
    "html": "CREDIT_MULTIPLIER_DOCUMENT",
    "txt": "CREDIT_MULTIPLIER_DOCUMENT",
    "rtf": "CREDIT_MULTIPLIER_DOCUMENT",
    "xlsx": "CREDIT_MULTIPLIER_DOCUMENT",
    "csv": "CREDIT_MULTIPLIER_DOCUMENT",
    # Audio
    "mp3": "CREDIT_MULTIPLIER_AUDIO",
    "wav": "CREDIT_MULTIPLIER_AUDIO",
    "flac": "CREDIT_MULTIPLIER_AUDIO",
    "ogg": "CREDIT_MULTIPLIER_AUDIO",
    "m4a": "CREDIT_MULTIPLIER_AUDIO",
    "aac": "CREDIT_MULTIPLIER_AUDIO",
    # Video
    "mp4": "CREDIT_MULTIPLIER_VIDEO",
    "avi": "CREDIT_MULTIPLIER_VIDEO",
    "mov": "CREDIT_MULTIPLIER_VIDEO",
    "mkv": "CREDIT_MULTIPLIER_VIDEO",
    "webm": "CREDIT_MULTIPLIER_VIDEO",
    "gif": "CREDIT_MULTIPLIER_VIDEO",
    # Images
    "jpeg": "CREDIT_MULTIPLIER_IMAGE",
    "jpg": "CREDIT_MULTIPLIER_IMAGE",
    "png": "CREDIT_MULTIPLIER_IMAGE",
    "webp": "CREDIT_MULTIPLIER_IMAGE",
    "svg": "CREDIT_MULTIPLIER_IMAGE",
    "bmp": "CREDIT_MULTIPLIER_IMAGE",
    # Ebooks
    "epub": "CREDIT_MULTIPLIER_EBOOK",
    "mobi": "CREDIT_MULTIPLIER_EBOOK",
    "azw3": "CREDIT_MULTIPLIER_EBOOK",
    # Archives
    "zip": "CREDIT_MULTIPLIER_ARCHIVE",
    "tar": "CREDIT_MULTIPLIER_ARCHIVE",
    "rar": "CREDIT_MULTIPLIER_ARCHIVE",
}

# Tier → discount setting name mapping
_TIER_DISCOUNT_MAP: dict[SubscriptionTier, str] = {
    SubscriptionTier.GUEST: "CREDIT_DISCOUNT_GUEST",
    SubscriptionTier.FREE: "CREDIT_DISCOUNT_FREE",
    SubscriptionTier.PREMIUM: "CREDIT_DISCOUNT_PRO",  # PREMIUM maps to PRO discount
}


def get_format_multiplier(source_format: str, target_format: str) -> float:
    """
    Return the complexity multiplier for a format pair.

    Uses the more expensive of the two formats' categories.

    Args:
        source_format: Source file format (e.g. 'pdf').
        target_format: Target file format (e.g. 'docx').

    Returns:
        Multiplier value (defaults to 1.0 for unknown formats).
    """
    settings = get_settings()
    source_cat = _FORMAT_CATEGORY_MAP.get(source_format.lower())
    target_cat = _FORMAT_CATEGORY_MAP.get(target_format.lower())

    multiplier = 1.0
    for cat_name in (source_cat, target_cat):
        if cat_name is not None:
            multiplier = max(multiplier, getattr(settings, cat_name, 1.0))

    return multiplier


def get_tier_discount(tier: SubscriptionTier) -> float:
    """
    Return the tier discount factor (0.0–1.0).

    Args:
        tier: User subscription tier.

    Returns:
        Discount factor where 1.0 = full price, 0.5 = 50% off.
    """
    settings = get_settings()
    setting_name = _TIER_DISCOUNT_MAP.get(tier, "CREDIT_DISCOUNT_FREE")
    return getattr(settings, setting_name, 1.0)


def calculate_credits(
    *,
    compute_duration_ms: int,
    source_format: str,
    target_format: str,
    tier: SubscriptionTier = SubscriptionTier.FREE,
    base_rate_per_second: float | None = None,
) -> int:
    """
    Calculate credits consumed based on worker compute time.

    Args:
        compute_duration_ms: Actual compute time in milliseconds.
        source_format: Source file format.
        target_format: Target file format.
        tier: User subscription tier (for discount).
        base_rate_per_second: Override the base rate (optional).

    Returns:
        Integer credits to charge (minimum 1).
    """
    if base_rate_per_second is None:
        base_rate_per_second = get_settings().CREDIT_BASE_RATE_PER_SECOND

    compute_seconds = compute_duration_ms / 1000.0
    multiplier = get_format_multiplier(source_format, target_format)
    discount = get_tier_discount(tier)

    raw = compute_seconds * base_rate_per_second * multiplier
    discounted = raw * discount

    return max(1, math.ceil(discounted))
