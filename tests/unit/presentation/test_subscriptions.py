"""Unit tests for the public subscription plans endpoint's AI entitlements.

The plans page is the marketing surface: it must show what the server actually
enforces, without naming the deployment's concrete models. These tests assert
both — the published numbers equal the ``assistant_policy`` the runtime enforces,
and no model id leaks to an anonymous caller.
"""

from fastapi.testclient import TestClient

from src.domain.assistant.policies.assistant_policy import (
    hourly_quota,
    max_actions_per_turn,
    max_attachments_for_tier,
    max_document_bytes_for_tier,
    model_label_for_tier,
    model_level_for_tier,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from tests.integration.dependencies.api_overrides import create_test_client

#: The API's plan tiers mapped to the domain tiers the policy is keyed by. They
#: are the same vocabulary; the map keeps the translation explicit.
_DOMAIN_TIER_BY_API_TIER: dict[str, SubscriptionTier] = {
    "FREE": SubscriptionTier.FREE,
    "PRO": SubscriptionTier.PRO,
    "PRO_PLUS": SubscriptionTier.PRO_PLUS,
    "ENTERPRISE": SubscriptionTier.ENTERPRISE,
}


def _plans(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/v1/subscription/plans")
    assert response.status_code == 200
    return {plan["tier"]: plan for plan in response.json()}


def test_the_four_plans_are_still_returned() -> None:
    with create_test_client() as client:
        response = client.get("/api/v1/subscription/plans")
    assert response.status_code == 200
    assert len(response.json()) == 4


def test_every_plan_publishes_an_ai_entitlement() -> None:
    with create_test_client() as client:
        plans = _plans(client)
    assert set(plans) == set(_DOMAIN_TIER_BY_API_TIER)
    for plan in plans.values():
        assert plan["ai"] is not None


def test_plan_ai_entitlement_matches_the_policy() -> None:
    """Every published number must equal what ``assistant_policy`` enforces."""
    with create_test_client() as client:
        plans = _plans(client)
    for api_tier, tier in _DOMAIN_TIER_BY_API_TIER.items():
        ai = plans[api_tier]["ai"]
        assert ai["model_level"] == model_level_for_tier(tier)
        assert ai["model_label"] == model_label_for_tier(tier)
        assert ai["requests_per_hour"] == hourly_quota(tier)
        assert ai["max_attachments"] == max_attachments_for_tier(tier)
        assert ai["max_document_mb"] == max_document_bytes_for_tier(tier) // (1024 * 1024)
        assert ai["max_actions_per_turn"] == max_actions_per_turn(tier)


def test_the_public_plans_page_never_exposes_a_model_id() -> None:
    """Only the level and its label are public — never a concrete model id."""
    with create_test_client() as client:
        plans = _plans(client)
    for plan in plans.values():
        assert set(plan["ai"]) == {
            "model_level",
            "model_label",
            "requests_per_hour",
            "max_attachments",
            "max_document_mb",
            "max_actions_per_turn",
        }


def test_paid_plans_advertise_a_better_or_equal_model_level_than_free() -> None:
    with create_test_client() as client:
        plans = _plans(client)
    rank = {"standard": 0, "advanced": 1, "priority": 2}
    order = ["FREE", "PRO", "PRO_PLUS", "ENTERPRISE"]
    levels = [rank[plans[tier]["ai"]["model_level"]] for tier in order]
    assert levels == sorted(levels)


def test_plan_ai_values_are_the_documented_ones() -> None:
    with create_test_client() as client:
        plans = _plans(client)
    assert plans["FREE"]["ai"] == {
        "model_level": "standard",
        "model_label": "Standard",
        "requests_per_hour": 20,
        "max_attachments": 1,
        "max_document_mb": 2,
        "max_actions_per_turn": 1,
    }
    assert plans["PRO"]["ai"]["requests_per_hour"] == 60
    assert plans["PRO"]["ai"]["max_document_mb"] == 25
    assert plans["PRO_PLUS"]["ai"]["max_document_mb"] == 25
    assert plans["ENTERPRISE"]["ai"]["max_actions_per_turn"] == 10
