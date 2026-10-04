"""Billing-UI additions: structured plan entitlements, cancellation state,
resume, and invoice history.

These three capabilities are purely additive on top of the existing
subscription API — an SPA that knows nothing about them must keep rendering
exactly what it rendered before. The interesting behaviour is therefore in the
*ordinary* states: FREE with no Stripe customer, Stripe unconfigured, and a
resume attempted with nothing to resume are all normal, not errors, and are
reported as such rather than raised.

The endpoint functions are called directly with fakes (the pattern used by
``test_payment_methods_endpoints.py`` and ``test_change_plan_endpoint.py``),
so the tests assert the route's own logic without an HTTP stack in the way.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.domain.subscriptions.value_object.tier import SubscriptionTier as DomainTier
from src.presentation.api.routers.v1.subscriptions import (
    get_subscription_status,
    list_invoices,
    list_plans,
    resume_subscription,
)
from src.presentation.schemas.subscription import (
    InvoiceListResponse,
    ResumeSubscriptionResponse,
    SubscriptionPlanResponse,
    SubscriptionStatus,
    SubscriptionStatusResponse,
)

#: Two unix timestamps used throughout; their UTC datetimes are asserted below
#: so a conversion regression (e.g. a naive datetime) is unmistakable.
_CREATED_TS = 1_750_000_000
_PERIOD_START_TS = 1_749_000_000
_PERIOD_END_TS = 1_751_000_000


class _FakeStripeService:
    """The narrow Stripe surface the three endpoints touch."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        remote_subscription: dict[str, Any] | None = None,
        resume_ok: bool = True,
        invoices: list[dict[str, Any]] | None = None,
    ) -> None:
        self.enabled = enabled
        self.remote_subscription = remote_subscription
        self.resume_ok = resume_ok
        self._invoices = list(invoices or [])
        self.resume_calls: list[str] = []
        self.invoice_calls: list[str] = []

    async def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        return self.remote_subscription

    async def resume_subscription(self, subscription_id: str) -> bool:
        self.resume_calls.append(subscription_id)
        return self.resume_ok

    async def list_invoices(self, customer_id: str) -> list[dict[str, Any]]:
        self.invoice_calls.append(customer_id)
        return list(self._invoices)


class _FakeSubscriptionRepo:
    def __init__(
        self,
        *,
        tier: DomainTier | None = DomainTier.PRO,
        subscription_id: str | None = "sub_1",
        customer_id: str | None = "cus_1",
    ) -> None:
        self._tier = tier
        self._subscription_id = subscription_id
        self._customer_id = customer_id

    async def get_subscription_row(self, user_id: int) -> Any:
        if self._tier is None:
            return None
        return SimpleNamespace(
            tier=self._tier,
            stripe_subscription_id=self._subscription_id,
            stripe_customer_id=self._customer_id,
        )


def _user(user_id: int = 7) -> Any:
    return SimpleNamespace(id=user_id)


# --- 1. structured plan entitlements ---------------------------------------


def _plans() -> dict[str, SubscriptionPlanResponse]:
    plans = asyncio.run(list_plans())
    return {plan.tier.value: plan for plan in plans}


def test_every_plan_publishes_the_three_structured_fields() -> None:
    plans = _plans()
    assert set(plans) == {"FREE", "PRO", "PRO_PLUS", "ENTERPRISE"}
    for plan in plans.values():
        assert hasattr(plan, "api_calls_month")
        assert hasattr(plan, "priority_processing")
        assert hasattr(plan, "support_level")


def test_structured_entitlements_agree_with_the_prose_features() -> None:
    """The structured values must mirror the ``features`` strings verbatim."""
    plans = _plans()

    assert plans["FREE"].api_calls_month == 10
    assert plans["FREE"].priority_processing is False
    assert plans["FREE"].support_level == "Community"
    assert any("10 API calls/month" in feature for feature in plans["FREE"].features)

    assert plans["PRO"].api_calls_month == 100
    assert plans["PRO"].priority_processing is False
    assert plans["PRO"].support_level == "Priority"
    assert any("100 API calls/month" in feature for feature in plans["PRO"].features)

    assert plans["PRO_PLUS"].api_calls_month == 1000
    assert plans["PRO_PLUS"].priority_processing is True
    assert plans["PRO_PLUS"].support_level == "24/7"
    assert any("Priority processing" in feature for feature in plans["PRO_PLUS"].features)

    # Unlimited API access has no integer representation; None means uncapped.
    assert plans["ENTERPRISE"].api_calls_month is None
    assert plans["ENTERPRISE"].priority_processing is True
    assert plans["ENTERPRISE"].support_level == "Dedicated"
    assert any("Dedicated support" in feature for feature in plans["ENTERPRISE"].features)


# --- 2. cancellation state on /status --------------------------------------


def _status(stripe: _FakeStripeService, repo: _FakeSubscriptionRepo) -> SubscriptionStatusResponse:
    return asyncio.run(
        get_subscription_status(
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def test_status_reports_a_scheduled_cancellation() -> None:
    stripe = _FakeStripeService(
        remote_subscription={
            "status": "active",
            "current_period_start": _PERIOD_START_TS,
            "current_period_end": _PERIOD_END_TS,
            "cancel_at_period_end": True,
        }
    )
    response = _status(stripe, _FakeSubscriptionRepo())

    assert response.status == SubscriptionStatus.ACTIVE
    assert response.cancel_at_period_end is True


def test_status_is_false_for_a_free_user() -> None:
    response = _status(_FakeStripeService(), _FakeSubscriptionRepo(tier=DomainTier.FREE))
    assert response.cancel_at_period_end is False


# --- 3. resume --------------------------------------------------------------


def _resume(
    stripe: _FakeStripeService, repo: _FakeSubscriptionRepo
) -> ResumeSubscriptionResponse:
    return asyncio.run(
        resume_subscription(
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def test_resume_without_a_subscription_is_a_400() -> None:
    stripe = _FakeStripeService()

    with pytest.raises(HTTPException) as exc:
        _resume(stripe, _FakeSubscriptionRepo(tier=DomainTier.FREE))

    assert exc.value.status_code == 400
    assert exc.value.detail == "No active subscription to resume"
    assert stripe.resume_calls == []


def test_resume_clears_the_cancellation_and_keeps_the_tier() -> None:
    stripe = _FakeStripeService()
    response = _resume(stripe, _FakeSubscriptionRepo(tier=DomainTier.PRO))

    assert stripe.resume_calls == ["sub_1"]
    assert response.cancel_at_period_end is False
    # The webhook remains the single writer for tier; resume must not change it.
    assert response.tier.value == "PRO"


def test_resume_failure_is_a_502() -> None:
    stripe = _FakeStripeService(resume_ok=False)

    with pytest.raises(HTTPException) as exc:
        _resume(stripe, _FakeSubscriptionRepo(tier=DomainTier.PRO))

    assert exc.value.status_code == 502
    assert exc.value.detail == "Stripe subscription could not be resumed"
    assert stripe.resume_calls == ["sub_1"]


# --- 4. invoices ------------------------------------------------------------


def _invoices(
    stripe: _FakeStripeService, repo: _FakeSubscriptionRepo
) -> InvoiceListResponse:
    return asyncio.run(
        list_invoices(
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def test_invoices_are_disabled_when_stripe_is_unconfigured() -> None:
    response = _invoices(_FakeStripeService(enabled=False), _FakeSubscriptionRepo())
    assert response.enabled is False
    assert response.invoices == []


def test_invoices_are_disabled_without_a_customer() -> None:
    # An ordinary state (a Free user has no customer until checkout), not an error.
    response = _invoices(_FakeStripeService(), _FakeSubscriptionRepo(customer_id=None))
    assert response.enabled is False
    assert response.invoices == []


def test_invoices_map_rows_and_preserve_urls() -> None:
    stripe = _FakeStripeService(
        invoices=[
            {
                "id": "in_1",
                "number": "INV-0001",
                "status": "paid",
                "amount_paid": 999,
                "amount_due": 0,
                "currency": "usd",
                "created": _CREATED_TS,
                "period_start": _PERIOD_START_TS,
                "period_end": _PERIOD_END_TS,
                "invoice_pdf": "https://pay.stripe.com/inv/in_1.pdf",
                "hosted_invoice_url": "https://pay.stripe.com/inv/in_1",
            },
            {
                "id": "in_2",
                "number": None,
                "status": "open",
                "amount_paid": None,
                "amount_due": None,
                "currency": None,
                "created": _CREATED_TS,
                "period_start": None,
                "period_end": None,
                "invoice_pdf": None,
                "hosted_invoice_url": None,
            },
        ]
    )
    response = _invoices(stripe, _FakeSubscriptionRepo(customer_id="cus_9"))

    assert response.enabled is True
    assert stripe.invoice_calls == ["cus_9"]
    assert [invoice.id for invoice in response.invoices] == ["in_1", "in_2"]

    first = response.invoices[0]
    assert first.created_at == datetime.fromtimestamp(_CREATED_TS, tz=UTC)
    assert first.period_start == datetime.fromtimestamp(_PERIOD_START_TS, tz=UTC)
    assert first.period_end == datetime.fromtimestamp(_PERIOD_END_TS, tz=UTC)
    assert first.created_at.tzinfo is UTC
    assert first.invoice_pdf == "https://pay.stripe.com/inv/in_1.pdf"
    assert first.hosted_invoice_url == "https://pay.stripe.com/inv/in_1"

    # Missing optionals fall back to the schema defaults, never crash.
    second = response.invoices[1]
    assert second.number is None
    assert second.amount_paid == 0
    assert second.amount_due == 0
    assert second.currency == "usd"
    assert second.period_start is None
    assert second.period_end is None
    assert second.invoice_pdf is None
    assert second.hosted_invoice_url is None
