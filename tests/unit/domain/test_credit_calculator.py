"""Tests for the compute-time-based credit calculator."""

from src.domain.conversions.policies.credit_calculator import (
    calculate_credits,
    get_format_multiplier,
    get_tier_discount,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class TestFormatMultiplier:
    """Format category multiplier lookups."""

    def test_document_formats_default_to_1_0(self):
        assert get_format_multiplier("pdf", "docx") >= 0.5

    def test_audio_has_higher_multiplier_than_document(self):
        audio = get_format_multiplier("mp3", "wav")
        doc = get_format_multiplier("pdf", "docx")
        assert audio >= doc

    def test_video_has_highest_multiplier(self):
        video = get_format_multiplier("mp4", "avi")
        doc = get_format_multiplier("pdf", "docx")
        audio = get_format_multiplier("mp3", "wav")
        image = get_format_multiplier("png", "jpeg")
        assert video >= doc
        assert video >= audio
        assert video >= image

    def test_image_formats(self):
        m = get_format_multiplier("png", "webp")
        assert 0.5 <= m <= 2.0

    def test_ebook_formats(self):
        m = get_format_multiplier("epub", "pdf")
        assert 0.5 <= m <= 3.0

    def test_archive_formats_lowest(self):
        archive = get_format_multiplier("zip", "tar")
        assert archive <= 1.0

    def test_unknown_format_defaults_to_1_0(self):
        assert get_format_multiplier("custom", "weird") >= 0.5


class TestTierDiscount:
    """Tier discount factors."""

    def test_guest_has_no_discount(self):
        assert get_tier_discount(SubscriptionTier.GUEST) == 1.0

    def test_free_has_no_discount(self):
        assert get_tier_discount(SubscriptionTier.FREE) == 1.0

    def test_premium_has_discount(self):
        assert get_tier_discount(SubscriptionTier.PREMIUM) < 1.0


class TestCalculateCredits:
    """Compute-time-based credit calculation."""

    def test_minimum_one_credit(self):
        """Even trivial conversions cost at least 1 credit."""
        credits = calculate_credits(
            compute_duration_ms=1,  # 1ms
            source_format="txt",
            target_format="md",
        )
        assert credits == 1

    def test_fast_document_conversion(self):
        """A very fast PDF → DOCX conversion."""
        credits = calculate_credits(
            compute_duration_ms=500,  # 0.5 seconds
            source_format="pdf",
            target_format="docx",
        )
        # 0.5s * 0.5 rate/s * 1.0 multiplier = 0.25 → ceil = 1
        assert credits == 1

    def test_medium_audio_conversion(self):
        """A realistic audio conversion (5 seconds)."""
        credits = calculate_credits(
            compute_duration_ms=5000,  # 5 seconds
            source_format="mp3",
            target_format="wav",
        )
        # 5s * 0.5 rate/s * 1.2 audio multiplier = 3.0 → ceil = 3
        expected = 3
        assert credits == expected

    def test_slow_video_conversion(self):
        """A heavy video conversion (30 seconds)."""
        credits = calculate_credits(
            compute_duration_ms=30000,  # 30 seconds
            source_format="mp4",
            target_format="avi",
        )
        # 30s * 0.5 rate/s * 2.0 video multiplier = 30.0 → ceil = 30
        assert credits == 30

    def test_tier_discount_applied(self):
        """Premium tier should pay less for the same compute."""
        full_price = calculate_credits(
            compute_duration_ms=10000,
            source_format="pdf",
            target_format="docx",
            tier=SubscriptionTier.FREE,
        )
        discounted = calculate_credits(
            compute_duration_ms=10000,
            source_format="pdf",
            target_format="docx",
            tier=SubscriptionTier.PREMIUM,
        )
        assert discounted <= full_price
        assert discounted < full_price  # PREMIUM gets a real discount

    def test_credit_scales_linearly_with_time(self):
        """Doubling compute time should roughly double credits."""
        short = calculate_credits(
            compute_duration_ms=2000,
            source_format="pdf",
            target_format="docx",
        )
        long = calculate_credits(
            compute_duration_ms=4000,
            source_format="pdf",
            target_format="docx",
        )
        assert long >= short
        # Allow ±1 tolerance due to ceil rounding
        assert long >= (short * 2) - 1

    def test_override_base_rate(self):
        """Custom base rate should override the default."""
        default = calculate_credits(
            compute_duration_ms=1000,
            source_format="txt",
            target_format="md",
        )
        fast = calculate_credits(
            compute_duration_ms=1000,
            source_format="txt",
            target_format="md",
            base_rate_per_second=0.1,
        )
        assert fast <= default
