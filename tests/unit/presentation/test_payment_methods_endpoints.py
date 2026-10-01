"""Saved-card management endpoints: list, set-default, remove.

The important behaviour is the delete guard: Stripe warns that detaching a
payment method used by an active subscription breaks that subscription, so the
default card of a live subscription must be refused (409) rather than silently
removed. Ownership is also enforced — a card id from another customer answers
404, indistinguishable from a missing one, so ids stay unenumerable.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.infrastructure.adapters.payment.stripe_service import SavedPaymentMethod
from src.presentation.api.routers.v1.subscriptions import (
    list_payment_methods,
    remove_payment_method,
    set_default_payment_method,
)
from src.presentation.schemas.subscription import PaymentMethodListResponse


def _card(method_id: str, *, is_default: bool = False) -> SavedPaymentMethod:
    return SavedPaymentMethod(
        id=method_id,
        brand="visa",
        last4="4242",
        exp_month=12,
        exp_year=2030,
        is_default=is_default,
    )


class _FakeStripeService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        methods: list[SavedPaymentMethod] | None = None,
        set_default_ok: bool = True,
        detach_ok: bool = True,
    ) -> None:
        self.enabled = enabled
        self._methods = list(methods or [])
        self.set_default_ok = set_default_ok
        self.detach_ok = detach_ok
        self.listed_for: list[str] = []
        self.default_calls: list[tuple[str, str, str | None]] = []
        self.detach_calls: list[str] = []

    async def list_payment_methods(self, customer_id: str) -> list[SavedPaymentMethod]:
        self.listed_for.append(customer_id)
        return list(self._methods)

    async def set_default_payment_method(
        self, customer_id: str, payment_method_id: str, subscription_id: str | None = None
    ) -> bool:
        self.default_calls.append((customer_id, payment_method_id, subscription_id))
        return self.set_default_ok

    async def detach_payment_method(self, payment_method_id: str) -> bool:
        self.detach_calls.append(payment_method_id)
        return self.detach_ok


class _FakeSubscriptionRepo:
    def __init__(self, customer_id: str | None, subscription_id: str | None = None) -> None:
        self._subscription_id = subscription_id
        self._customer_id = customer_id

    async def get_subscription_row(self, user_id: int) -> Any:
        if self._customer_id is None:
            return None
        return SimpleNamespace(
            stripe_customer_id=self._customer_id,
            stripe_subscription_id=self._subscription_id,
        )


def _user(user_id: int = 7) -> Any:
    return SimpleNamespace(id=user_id)


def _list(stripe: _FakeStripeService, repo: _FakeSubscriptionRepo) -> PaymentMethodListResponse:
    return asyncio.run(
        list_payment_methods(
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def _set_default(
    method_id: str, stripe: _FakeStripeService, repo: _FakeSubscriptionRepo
) -> PaymentMethodListResponse:
    return asyncio.run(
        set_default_payment_method(
            method_id,
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def _remove(
    method_id: str, stripe: _FakeStripeService, repo: _FakeSubscriptionRepo
) -> PaymentMethodListResponse:
    return asyncio.run(
        remove_payment_method(
            method_id,
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


# --- list ---------------------------------------------------------------


def test_list_degrades_quietly_when_stripe_is_unconfigured() -> None:
    response = _list(_FakeStripeService(enabled=False), _FakeSubscriptionRepo("cus_1"))
    assert response.enabled is False
    assert response.methods == []


def test_list_degrades_quietly_without_a_customer() -> None:
    response = _list(_FakeStripeService(), _FakeSubscriptionRepo(None))
    assert response.enabled is False
    assert response.methods == []


def test_list_returns_the_customers_cards_with_the_default_flagged() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1", is_default=True), _card("pm_2")])
    response = _list(stripe, _FakeSubscriptionRepo("cus_1"))

    assert response.enabled is True
    assert [method.id for method in response.methods] == ["pm_1", "pm_2"]
    assert response.methods[0].is_default is True
    assert stripe.listed_for == ["cus_1"]


# --- set default --------------------------------------------------------


def test_set_default_passes_the_subscription_id_so_renewals_use_it() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1"), _card("pm_2")])
    repo = _FakeSubscriptionRepo("cus_1", subscription_id="sub_1")

    _set_default("pm_2", stripe, repo)

    assert stripe.default_calls == [("cus_1", "pm_2", "sub_1")]


def test_set_default_of_a_foreign_card_is_404() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1")])

    with pytest.raises(HTTPException) as exc:
        _set_default("pm_other", stripe, _FakeSubscriptionRepo("cus_1"))

    assert exc.value.status_code == 404
    assert stripe.default_calls == []


def test_set_default_failure_is_a_502() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1")], set_default_ok=False)

    with pytest.raises(HTTPException) as exc:
        _set_default("pm_1", stripe, _FakeSubscriptionRepo("cus_1"))

    assert exc.value.status_code == 502


# --- remove -------------------------------------------------------------


def test_remove_refuses_the_default_card_of_an_active_subscription() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1", is_default=True)])
    repo = _FakeSubscriptionRepo("cus_1", subscription_id="sub_1")

    with pytest.raises(HTTPException) as exc:
        _remove("pm_1", stripe, repo)

    assert exc.value.status_code == 409
    assert stripe.detach_calls == []


def test_remove_allows_the_default_card_when_there_is_no_active_subscription() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1", is_default=True)])
    repo = _FakeSubscriptionRepo("cus_1", subscription_id=None)

    _remove("pm_1", stripe, repo)

    assert stripe.detach_calls == ["pm_1"]


def test_remove_allows_a_non_default_card_of_an_active_subscription() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1", is_default=True), _card("pm_2")])
    repo = _FakeSubscriptionRepo("cus_1", subscription_id="sub_1")

    _remove("pm_2", stripe, repo)

    assert stripe.detach_calls == ["pm_2"]


def test_remove_of_a_foreign_card_is_404() -> None:
    stripe = _FakeStripeService(methods=[_card("pm_1")])

    with pytest.raises(HTTPException) as exc:
        _remove("pm_other", stripe, _FakeSubscriptionRepo("cus_1"))

    assert exc.value.status_code == 404
    assert stripe.detach_calls == []
