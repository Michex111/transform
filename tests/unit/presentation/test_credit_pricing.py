"""Tests for credit bundle pricing."""

from src.presentation.api.routers.v1.credits import _price_for_amount


def test_exact_bundle_price() -> None:
    assert _price_for_amount(100) == 10.0
    assert _price_for_amount(500) == 40.0
    assert _price_for_amount(1000) == 70.0
    assert _price_for_amount(5000) == 300.0


def test_non_bundle_amount_uses_largest_smaller_bundle_rate() -> None:
    # 250 credits → uses the 100-bundle rate (0.10/credit)
    assert _price_for_amount(250) == 25.0


def test_amount_above_all_bundles() -> None:
    # 10000 credits → uses the 5000-bundle rate (0.06/credit)
    assert _price_for_amount(10000) == 600.0
