"""The Customer Session endpoint: in-app payment method management.

Replaces the Customer Portal's card screen, which cannot be branded. What
matters is that the two ordinary "no customer yet" states degrade quietly
(``enabled=False``) instead of raising, because a Free user genuinely has no
Stripe customer until their first checkout — and a 500 there would look like a
broken billing page rather than the normal state it is.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from src.presentation.api.routers.v1.subscriptions import create_payment_method_session
from src.presentation.schemas.subscription import PaymentMethodSessionResponse


class _FakeStripeService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        client_secret: str | None = "cs_seti_1",
        setup_intent_secret: str | None = "seti_secret_1",
    ) -> None:
        self.enabled = enabled
        self._client_secret = client_secret
        self._setup_intent_secret = setup_intent_secret
        self.customer_sessions: list[str] = []
        self.setup_intents: list[str] = []

    async def create_customer_session(self, customer_id: str) -> str | None:
        self.customer_sessions.append(customer_id)
        return self._client_secret

    async def create_setup_intent(self, customer_id: str) -> str | None:
        self.setup_intents.append(customer_id)
        return self._setup_intent_secret


class _FakeSubscriptionRepo:
    def __init__(self, stripe_customer_id: str | None) -> None:
        self._row = (
            None
            if stripe_customer_id is None
            else SimpleNamespace(stripe_customer_id=stripe_customer_id)
        )

    async def get_subscription_row(self, user_id: int):
        return self._row


def _user(user_id: int = 7):
    return SimpleNamespace(id=user_id)


def _call(stripe, repo) -> PaymentMethodSessionResponse:
    # Duck-typed doubles, cast at the injection boundary: a real `UserModel`
    # would drag in the whole SQLAlchemy mapper for a test that only reads
    # `current_user.id`, and the route's declared type is about production
    # safety rather than what the double must satisfy. Matches the convention
    # used by the other direct-router-call tests in this directory.
    return asyncio.run(
        create_payment_method_session(
            current_user=cast(Any, _user()),
            subscription_repo=cast(Any, repo),
            stripe_service=cast(Any, stripe),
        )
    )


def test_returns_a_client_secret_for_an_existing_stripe_customer() -> None:
    stripe = _FakeStripeService(client_secret="cs_seti_abc")
    repo = _FakeSubscriptionRepo("cus_123")

    response = _call(stripe, repo)

    assert response.client_secret == "cs_seti_abc"
    assert response.enabled is True
    assert stripe.customer_sessions == ["cus_123"]


def test_returns_a_setup_intent_secret_alongside_the_session() -> None:
    """Both secrets are needed: the session says which cards exist and whether
    a new one may be saved, the SetupIntent is what the new card attaches to."""
    stripe = _FakeStripeService(setup_intent_secret="seti_secret_xyz")
    repo = _FakeSubscriptionRepo("cus_123")

    response = _call(stripe, repo)

    assert response.setup_intent_client_secret == "seti_secret_xyz"
    assert stripe.setup_intents == ["cus_123"]


def test_a_failed_setup_intent_still_returns_the_session() -> None:
    """The Payment Element can create its own intent at confirmation time, so
    this degrades to that mode instead of losing card management entirely."""
    stripe = _FakeStripeService(setup_intent_secret=None)
    repo = _FakeSubscriptionRepo("cus_123")

    response = _call(stripe, repo)

    assert response.enabled is True
    assert response.client_secret == "cs_seti_1"
    assert response.setup_intent_client_secret is None


def test_a_free_user_without_a_customer_degrades_quietly() -> None:
    """A Free user has no customer until their first checkout. That is an
    ordinary state, so it must not raise — the SPA just hides the section."""
    stripe = _FakeStripeService()
    repo = _FakeSubscriptionRepo(None)

    response = _call(stripe, repo)

    assert response.enabled is False
    assert response.client_secret is None
    assert stripe.customer_sessions == []


def test_an_absent_subscription_row_degrades_quietly() -> None:
    # get_subscription_row returns None for a user who has never subscribed.
    stripe = _FakeStripeService()
    repo = _FakeSubscriptionRepo(None)

    response = _call(stripe, repo)

    assert response.enabled is False
    assert stripe.customer_sessions == []


def test_unconfigured_stripe_degrades_quietly() -> None:
    stripe = _FakeStripeService(enabled=False)
    repo = _FakeSubscriptionRepo("cus_123")

    response = _call(stripe, repo)

    assert response.enabled is False
    assert stripe.customer_sessions == []


def test_a_stripe_failure_is_a_502_not_a_silent_success() -> None:
    """A configured Stripe that returns nothing is a real fault: reporting
    ``enabled=False`` would hide the card section as though all were well."""
    stripe = _FakeStripeService(client_secret=None)
    repo = _FakeSubscriptionRepo("cus_123")

    with pytest.raises(HTTPException) as exc:
        _call(stripe, repo)

    assert exc.value.status_code == 502
