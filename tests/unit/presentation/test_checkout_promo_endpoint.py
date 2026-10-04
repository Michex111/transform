"""``POST /api/v1/subscription/checkout`` with an optional promotion code.

These drive the real router function against a real ``StripeService`` whose SDK
client is a recorder, so a single case covers the route's validation *and* the
service's lookup/param wiring. The load-bearing case is the invalid code: it
must be a 400 and must never reach ``checkout.sessions.create`` — silently
charging full price when a student typed a code is the worst outcome, and a 500
is not much better.

Blank handling is the other edge: an empty/whitespace code is ABSENT (no lookup
at all), not an invalid code.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.config.settings import get_settings
from src.presentation.api.routers.v1.subscriptions import (
    create_checkout_session as checkout_endpoint,
)
from src.presentation.schemas.subscription import (
    CheckoutRequest,
    CheckoutResponse,
    SubscriptionTier,
)

_INVALID_DETAIL = "That promotion code isn't valid or has expired."

#: The complete CheckoutResponse surface as it now appears on the wire. Pinned
#: so a field added later without the SPA's knowledge is a visible change.
_WIRE_FIELDS = {
    "checkout_url",
    "client_secret",
    "amount_total",
    "currency",
    "discount_code",
    "discount_percent_off",
    "discount_duration",
}


def _expanded_discount() -> Any:
    coupon = SimpleNamespace(percent_off=100.0, duration="once")
    return SimpleNamespace(
        promotion_code=SimpleNamespace(id="promo_student1", code="STUDENT1", coupon=coupon),
        coupon=coupon,
    )

class _RecordingPromotionCodes:
    def __init__(self, matches: list[Any]) -> None:
        self.matches = matches
        self.lookups: list[dict[str, Any]] = []

    def list(self, params: dict[str, Any]) -> Any:
        self.lookups.append(params)
        # Real shape: a `ListObject` envelope, read via `_stripe_list`.
        return SimpleNamespace(data=self.matches)


class _RecordingSessions:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.calls: list[dict[str, Any]] = []

    def create(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        return self.session


class _FakeSubscriptionRepo:
    async def get_subscription_row(self, user_id: str) -> Any:
        del user_id
        return None


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_PRO_PLUS", "price_pro_plus")
    monkeypatch.delenv("STRIPE_CHECKOUT_UI_MODE", raising=False)
    get_settings.cache_clear()

    service = StripeService()
    promotion_codes = _RecordingPromotionCodes(
        [
            SimpleNamespace(
                id="promo_student1",
                code="STUDENT1",
                active=True,
                # The coupon is nested under `promotion` — a PromotionCode has
                # no top-level `coupon` field in this API version. Present
                # because the resolver asks for `expand=["data.promotion.coupon"]`.
                promotion=SimpleNamespace(
                    type="coupon",
                    coupon=SimpleNamespace(percent_off=100.0, duration="once"),
                ),
            )
        ]
    )
    sessions = _RecordingSessions(
        SimpleNamespace(
            id="cs_promo",
            url="https://checkout.stripe.com/c/pay/cs_promo",
            client_secret="cs_promo_secret",
            amount_total=0,
            currency="usd",
            discounts=[_expanded_discount()],
        )
    )
    service._client = SimpleNamespace(
        v1=SimpleNamespace(
            checkout=SimpleNamespace(sessions=sessions),
            promotion_codes=promotion_codes,
        )
    )
    yield service, promotion_codes, sessions
    get_settings.cache_clear()


def _call(service: StripeService, body: CheckoutRequest) -> CheckoutResponse:
    # Duck-typed doubles at the injection boundary, matching the other direct
    # router-call tests in this directory.
    return asyncio.run(
        checkout_endpoint(
            payload=body,
            current_user=cast(Any, SimpleNamespace(id=7, email="student@example.com")),
            subscription_repo=cast(Any, _FakeSubscriptionRepo()),
            stripe_service=cast(Any, service),
        )
    )


def test_a_valid_code_is_resolved_and_applied(stripe: Any) -> None:
    service, promotion_codes, sessions = stripe

    _call(service, CheckoutRequest(tier=SubscriptionTier.PRO, promotion_code="STUDENT1"))

    assert promotion_codes.lookups == [
        {
            "code": "STUDENT1",
            "active": True,
            "limit": 1,
            "expand": ["data.promotion.coupon"],
        }
    ]
    assert sessions.calls[0]["discounts"] == [{"promotion_code": "promo_student1"}]
    assert sessions.calls[0]["payment_method_collection"] == "if_required"


def test_a_valid_code_is_reported_back_in_the_response(stripe: Any) -> None:
    service, _, _ = stripe

    response = _call(
        service, CheckoutRequest(tier=SubscriptionTier.PRO, promotion_code="STUDENT1")
    )

    assert set(response.model_dump()) == _WIRE_FIELDS
    assert response.checkout_url == "https://checkout.stripe.com/c/pay/cs_promo"
    assert response.client_secret is None
    assert response.amount_total == 0
    assert response.currency == "usd"
    assert response.discount_code == "STUDENT1"


def test_the_discount_facts_survive_an_un_expanded_session(stripe: Any) -> None:
    """End-to-end guard for the shape Stripe ACTUALLY returns.

    By default ``discounts[].promotion_code`` is a bare ``promo_…`` id and a
    resolved ``Discount`` carries no ``percent_off``/``duration`` at all. An
    earlier version read those facts off the session, so against real Stripe
    every field came back ``None`` and the SPA rendered no discount block — a
    silently dead feature. The fixture's *expanded* discount hid it.

    Here the session reports the realistic un-expanded shape, and the response
    must still describe the discount, because the facts came from the lookup.
    """
    service, _, sessions = stripe
    sessions.session = SimpleNamespace(
        id="cs_promo",
        url="https://checkout.stripe.com/c/pay/cs_promo",
        client_secret="cs_promo_secret",
        amount_total=0,
        currency="usd",
        discounts=[SimpleNamespace(id="di_1", promotion_code="promo_student1")],
    )

    response = _call(
        service, CheckoutRequest(tier=SubscriptionTier.PRO, promotion_code="STUDENT1")
    )

    assert response.amount_total == 0
    assert response.currency == "usd"
    assert response.discount_code == "STUDENT1"
    assert response.discount_percent_off == 100.0
    assert response.discount_duration == "once"
    assert response.discount_percent_off == 100.0
    assert response.discount_duration == "once"


def test_an_invalid_code_is_a_400_and_no_session_is_created(stripe: Any) -> None:
    service, promotion_codes, sessions = stripe
    promotion_codes.matches = []  # unknown / expired / inactive

    with pytest.raises(HTTPException) as exc:
        _call(
            service,
            CheckoutRequest(tier=SubscriptionTier.PRO, promotion_code="EXPIRED"),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == _INVALID_DETAIL
    # The whole point: the rejection happens before any session exists.
    assert sessions.calls == []


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_code_is_absent_and_never_looked_up(stripe: Any, blank: str) -> None:
    service, promotion_codes, sessions = stripe

    response = _call(
        service, CheckoutRequest(tier=SubscriptionTier.PRO, promotion_code=blank)
    )

    assert promotion_codes.lookups == []
    assert "discounts" not in sessions.calls[0]
    assert response.checkout_url == "https://checkout.stripe.com/c/pay/cs_promo"


def test_no_promo_field_at_all_keeps_the_existing_behaviour(stripe: Any) -> None:
    service, promotion_codes, sessions = stripe
    sessions.session.discounts = None

    response = _call(service, CheckoutRequest(tier=SubscriptionTier.PRO))

    assert promotion_codes.lookups == []
    assert "discounts" not in sessions.calls[0]
    assert sessions.calls[0]["payment_method_collection"] == "if_required"
    assert response.discount_code is None
    assert response.discount_percent_off is None
    assert response.discount_duration is None
