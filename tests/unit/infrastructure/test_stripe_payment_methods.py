"""``StripeService``'s card-management paths.

Three things here are load-bearing and none of them are visible from the SPA:

* **Which card is \"the default\".** A renewal charges the *subscription's*
  ``default_payment_method`` and only falls back to the customer's
  ``invoice_settings`` when the subscription has none. Reading the customer side
  alone can badge a card the next invoice will not use, so the subscription has
  to win when it says something.
* **Which features the Customer Session enables.** ``payment_method_save`` with
  no ``payment_method_save_usage`` saves the card with the default
  (on-session) usage, which is the wrong semantic for a subscription that is
  charged while the customer is away; Stripe's own sample sets it to
  ``off_session``.
* **The SetupIntent.** Saving a card without a payment is documented as a
  server-created SetupIntent, and a failure there must degrade quietly rather
  than take the card screen down.

The SDK takes params as a DICT positional argument — a keyword call would only
fail in production — so the fakes below record exactly what was passed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.config.settings import get_settings


class _Recorder:
    """Records its params and answers with a fixed value or raises."""

    def __init__(self, value: Any = None, error: Exception | None = None) -> None:
        self.calls: list[Any] = []
        self._value = value
        self._error = error

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        if self._error is not None:
            raise self._error
        return self._value


def _card(
    method_id: str,
    *,
    wallet: str | None = None,
    brand: str = "visa",
    last4: str = "4242",
) -> Any:
    card: dict[str, Any] = {"brand": brand, "last4": last4, "exp_month": 12, "exp_year": 2030}
    if wallet:
        card["wallet"] = {"type": wallet}
    return SimpleNamespace(id=method_id, card=SimpleNamespace(**card))


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real ``StripeService`` whose SDK client is a set of recorders."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    get_settings.cache_clear()

    service = StripeService()
    service._client = SimpleNamespace(
        v1=SimpleNamespace(
            customers=SimpleNamespace(
                retrieve=_Recorder(
                    SimpleNamespace(
                        invoice_settings=SimpleNamespace(default_payment_method="pm_customer")
                    )
                )
            ),
            subscriptions=SimpleNamespace(
                retrieve=_Recorder(
                    SimpleNamespace(default_payment_method="pm_subscription")
                )
            ),
            payment_methods=SimpleNamespace(
                list=_Recorder(
                    SimpleNamespace(data=[_card("pm_customer"), _card("pm_subscription")])
                )
            ),
            setup_intents=SimpleNamespace(
                create=_Recorder(SimpleNamespace(client_secret="seti_secret_1"))
            ),
            customer_sessions=SimpleNamespace(
                create=_Recorder(SimpleNamespace(client_secret="cs_seti_1"))
            ),
        )
    )
    yield service

    get_settings.cache_clear()


def test_disabled_service_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert asyncio.run(StripeService().list_payment_methods("cus_1")) == []
    finally:
        get_settings.cache_clear()


def test_the_subscription_default_wins_over_the_customer_default(stripe: Any) -> None:
    """The whole point of passing the subscription id through."""
    methods = asyncio.run(
        stripe.list_payment_methods("cus_1", subscription_id="sub_1")
    )

    by_id = {method.id: method for method in methods}
    assert by_id["pm_subscription"].is_default is True
    assert by_id["pm_customer"].is_default is False


def test_the_customer_default_is_used_when_no_subscription_is_given(stripe: Any) -> None:
    methods = asyncio.run(stripe.list_payment_methods("cus_1"))

    by_id = {method.id: method for method in methods}
    assert by_id["pm_customer"].is_default is True
    assert by_id["pm_subscription"].is_default is False


def test_a_subscription_that_states_no_default_falls_back_to_the_customer(stripe: Any) -> None:
    """A subscription with no pinned card charges the customer default, so the
    badge must follow it rather than marking nothing at all."""
    stripe._client.v1.subscriptions.retrieve = _Recorder(
        SimpleNamespace(default_payment_method=None)
    )

    methods = asyncio.run(stripe.list_payment_methods("cus_1", subscription_id="sub_1"))

    by_id = {method.id: method for method in methods}
    assert by_id["pm_customer"].is_default is True


def test_an_unreadable_subscription_does_not_lose_the_card_list(stripe: Any) -> None:
    stripe._client.v1.subscriptions.retrieve = _Recorder(error=RuntimeError("stripe is down"))

    methods = asyncio.run(stripe.list_payment_methods("cus_1", subscription_id="sub_1"))

    # Falls back to the customer default rather than returning nothing.
    assert {method.id for method in methods} == {"pm_customer", "pm_subscription"}
    assert {m.id for m in methods if m.is_default} == {"pm_customer"}


def test_a_wallet_token_is_reported_and_a_plain_card_is_not(stripe: Any) -> None:
    stripe._client.v1.payment_methods.list = _Recorder(
        SimpleNamespace(data=[_card("pm_wallet", wallet="apple_pay"), _card("pm_plain")])
    )

    methods = asyncio.run(stripe.list_payment_methods("cus_1"))

    by_id = {method.id: method for method in methods}
    assert by_id["pm_wallet"].wallet == "apple_pay"
    assert by_id["pm_plain"].wallet is None


def test_customer_session_asks_for_off_session_saving(stripe: Any) -> None:
    """Without `payment_method_save_usage` the saved card keeps the default
    on-session usage, which is not how a subscription is charged."""
    secret = asyncio.run(stripe.create_customer_session("cus_1"))

    assert secret == "cs_seti_1"
    (params,) = stripe._client.v1.customer_sessions.create.calls[0]
    features = params["components"]["payment_element"]["features"]
    assert params["customer"] == "cus_1"
    assert features["payment_method_save"] == "enabled"
    assert features["payment_method_redisplay"] == "enabled"
    assert features["payment_method_save_usage"] == "off_session"
    # Removal stays off: detaching a card breaks an active subscription, and the
    # server-side endpoint enforces the 409 guard the Element would bypass.
    assert "payment_method_remove" not in features


def test_a_customer_session_failure_is_none_not_an_raise(stripe: Any) -> None:
    stripe._client.v1.customer_sessions.create = _Recorder(error=RuntimeError("stripe is down"))

    assert asyncio.run(stripe.create_customer_session("cus_1")) is None


def test_setup_intent_is_created_for_the_customer_cards_only(stripe: Any) -> None:
    """Cards only, even though the account accepts many more methods.

    Verified live against the dev account: with ``automatic_payment_methods`` the
    intent carried card/bancontact/klarna/link/blik/pix/satispay, so the form
    would offer seven ways to save something this screen can never list. A
    customer who picked Klarna would see the card vanish straight after saving.
    Apple Pay / Google Pay / Link ride on the ``card`` type, so wallets still work.
    """
    secret = asyncio.run(stripe.create_setup_intent("cus_1"))

    assert secret == "seti_secret_1"
    (params,) = stripe._client.v1.setup_intents.create.calls[0]
    assert params["customer"] == "cus_1"
    assert params["payment_method_types"] == ["card"]
    # And never the automatic set, which would re-open every Dashboard method.
    assert "automatic_payment_methods" not in params


def test_a_setup_intent_failure_is_none_so_the_caller_can_fall_back(stripe: Any) -> None:
    stripe._client.v1.setup_intents.create = _Recorder(error=RuntimeError("stripe is down"))

    assert asyncio.run(stripe.create_setup_intent("cus_1")) is None


def test_a_setup_intent_without_a_secret_is_none(stripe: Any) -> None:
    stripe._client.v1.setup_intents.create = _Recorder(SimpleNamespace(client_secret=None))

    assert asyncio.run(stripe.create_setup_intent("cus_1")) is None
