"""Policy tests for the assistant's per-tier allowances.

These numbers are enforced by the service/toolbox AND published by
``/assistant/status`` and ``/subscription/plans``, so getting them wrong makes
the product either lie to users or hand out unentitled capacity. The tests
therefore pin the concrete values, monotonicity between plans, and the
fail-closed behaviour for a tier this build does not know.
"""

from typing import cast

from src.domain.assistant.policies.assistant_policy import (
    AI_REQUESTS_PER_HOUR,
    MODEL_LEVEL_LABELS,
    can_use_assistant,
    hourly_quota,
    max_actions_per_turn,
    max_attachments_for_tier,
    max_document_bytes_for_tier,
    max_tool_iterations_for_tier,
    model_label_for_tier,
    model_level_for_tier,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier

#: Cheapest to most expensive, used to assert nothing gets worse as a plan
#: gets better.
_TIER_ORDER = [
    SubscriptionTier.FREE,
    SubscriptionTier.PRO,
    SubscriptionTier.PRO_PLUS,
    SubscriptionTier.ENTERPRISE,
]


def _unknown() -> SubscriptionTier:
    """A tier this build does not know about (e.g. a row from a newer release)."""
    return cast(SubscriptionTier, "SOMETHING_NEW")


def test_every_tier_has_an_entry() -> None:
    """A tier missing from the table would silently fall back to 0 (no access).

    Asserting completeness means adding a tier to the enum forces a deliberate
    decision here rather than locking it out of the assistant by omission.
    """
    assert set(AI_REQUESTS_PER_HOUR) == set(SubscriptionTier)


def test_guest_has_no_assistant_access() -> None:
    assert hourly_quota(SubscriptionTier.GUEST) == 0
    assert can_use_assistant(SubscriptionTier.GUEST) is False


def test_free_tier_gets_a_small_allowance() -> None:
    assert hourly_quota(SubscriptionTier.FREE) == 20
    assert can_use_assistant(SubscriptionTier.FREE) is True


def test_paid_tiers_are_monotonic_on_requests_per_hour() -> None:
    """A more expensive plan must never get a smaller quota."""
    quotas = [hourly_quota(tier) for tier in _TIER_ORDER]
    assert quotas == sorted(quotas)
    assert quotas[0] < quotas[-1]


def test_legacy_premium_matches_pro() -> None:
    """PREMIUM is a legacy alias for the same price point, not a new tier."""
    assert hourly_quota(SubscriptionTier.PREMIUM) == hourly_quota(SubscriptionTier.PRO)
    assert model_level_for_tier(SubscriptionTier.PREMIUM) == model_level_for_tier(
        SubscriptionTier.PRO
    )
    assert max_attachments_for_tier(SubscriptionTier.PREMIUM) == max_attachments_for_tier(
        SubscriptionTier.PRO
    )
    assert max_actions_per_turn(SubscriptionTier.PREMIUM) == max_actions_per_turn(
        SubscriptionTier.PRO
    )
    assert max_tool_iterations_for_tier(
        SubscriptionTier.PREMIUM
    ) == max_tool_iterations_for_tier(SubscriptionTier.PRO)
    # The document budget is the dimension where a drift is invisible: PREMIUM
    # is not sold any more, so a legacy row silently reading less than the Pro
    # plan it was sold as would never show up on the pricing page.
    assert max_document_bytes_for_tier(
        SubscriptionTier.PREMIUM
    ) == max_document_bytes_for_tier(SubscriptionTier.PRO)


def test_unknown_tier_is_unavailable() -> None:
    """An unrecognised tier fails closed rather than being granted access."""
    unknown = _unknown()
    assert hourly_quota(unknown) == 0
    assert can_use_assistant(unknown) is False


def test_unknown_tier_gets_the_guest_allowances_not_a_generous_one() -> None:
    """A tier this build does not know must get the most restrictive values."""
    unknown = _unknown()
    assert model_level_for_tier(unknown) == model_level_for_tier(SubscriptionTier.GUEST)
    assert model_label_for_tier(unknown) == model_label_for_tier(SubscriptionTier.GUEST)
    assert max_attachments_for_tier(unknown) == max_attachments_for_tier(
        SubscriptionTier.GUEST
    )
    assert max_document_bytes_for_tier(unknown) == max_document_bytes_for_tier(
        SubscriptionTier.GUEST
    )
    assert max_actions_per_turn(unknown) == max_actions_per_turn(SubscriptionTier.GUEST)
    assert max_tool_iterations_for_tier(unknown) == max_tool_iterations_for_tier(
        SubscriptionTier.GUEST
    )


def test_guest_is_zero_on_every_dimension() -> None:
    """GUEST has no assistant at all, so every budget is 0."""
    guest = SubscriptionTier.GUEST
    assert hourly_quota(guest) == 0
    assert max_attachments_for_tier(guest) == 0
    assert max_document_bytes_for_tier(guest) == 0
    assert max_actions_per_turn(guest) == 0
    assert max_tool_iterations_for_tier(guest) == 0


# ---------------------------------------------------------------------------
# Model levels
# ---------------------------------------------------------------------------


def test_model_level_per_tier() -> None:
    assert model_level_for_tier(SubscriptionTier.GUEST) == "standard"
    assert model_level_for_tier(SubscriptionTier.FREE) == "standard"
    assert model_level_for_tier(SubscriptionTier.PREMIUM) == "advanced"
    assert model_level_for_tier(SubscriptionTier.PRO) == "advanced"
    assert model_level_for_tier(SubscriptionTier.PRO_PLUS) == "priority"
    assert model_level_for_tier(SubscriptionTier.ENTERPRISE) == "priority"


def test_every_model_level_has_a_label() -> None:
    """A new level without a label would KeyError ``model_label_for_tier``."""
    for tier in SubscriptionTier:
        assert model_label_for_tier(tier) in MODEL_LEVEL_LABELS.values()


def test_model_labels_match_their_levels() -> None:
    assert model_label_for_tier(SubscriptionTier.FREE) == "Standard"
    assert model_label_for_tier(SubscriptionTier.PRO) == "Advanced"
    assert model_label_for_tier(SubscriptionTier.PRO_PLUS) == "Priority"


# ---------------------------------------------------------------------------
# Monotonicity across every dimension
# ---------------------------------------------------------------------------


def test_no_dimension_gets_worse_as_the_plan_gets_better() -> None:
    """A higher plan is never worse than a lower one on any dimension."""
    rank = {"standard": 0, "advanced": 1, "priority": 2}
    level_ranks = [rank[model_level_for_tier(tier)] for tier in _TIER_ORDER]
    assert level_ranks == sorted(level_ranks)

    for accessor in (
        hourly_quota,
        max_attachments_for_tier,
        max_document_bytes_for_tier,
        max_actions_per_turn,
        max_tool_iterations_for_tier,
    ):
        values = [accessor(tier) for tier in _TIER_ORDER]
        assert values == sorted(values), accessor.__name__


# ---------------------------------------------------------------------------
# Concrete values (the key deliverable, asserted so a silent edit is caught)
# ---------------------------------------------------------------------------


def test_entitlement_values() -> None:
    free, pro, pro_plus, enterprise = _TIER_ORDER
    assert max_attachments_for_tier(free) == 1
    assert max_attachments_for_tier(pro) == 3
    assert max_attachments_for_tier(pro_plus) == 5
    assert max_attachments_for_tier(enterprise) == 5

    assert max_document_bytes_for_tier(free) == 2 * 1024 * 1024
    assert max_document_bytes_for_tier(pro) == 25 * 1024 * 1024
    assert max_document_bytes_for_tier(pro_plus) == 25 * 1024 * 1024
    assert max_document_bytes_for_tier(enterprise) == 25 * 1024 * 1024

    assert max_actions_per_turn(free) == 1
    assert max_actions_per_turn(pro) == 3
    assert max_actions_per_turn(pro_plus) == 5
    assert max_actions_per_turn(enterprise) == 10

    assert max_tool_iterations_for_tier(free) == 4
    assert max_tool_iterations_for_tier(pro) == 6
    assert max_tool_iterations_for_tier(pro_plus) == 8
    assert max_tool_iterations_for_tier(enterprise) == 8
