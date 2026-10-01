"""In-place plan changes: ``StripeService.change_subscription_plan``.

The hazard these tests exist for: ``create_checkout_session`` ALWAYS creates a
new subscription, so using it for an upgrade would bill a customer who already
has one for a second, concurrent subscription. A plan change must go through
``subscriptions.update`` on the existing id instead.

The second hazard is ordering: an immediate upgrade moves
``current_period_end``, so the OLD value must be captured *before* the update —
that value is the carryover's expiry.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.config.settings import get_settings

_OLD_PERIOD_END = 1_800_000_000
_NEW_PERIOD_END = 1_900_000_000


def _subscription(*, period_end: int = _OLD_PERIOD_END, schedule: str | None = None) -> Any:
    return SimpleNamespace(
        id="sub_1",
        current_period_end=period_end,
        schedule=schedule,
        items=SimpleNamespace(
            data=[SimpleNamespace(id="si_1", price="price_pro", quantity=1)]
        ),
    )


class _RecordingSubscriptions:
    def __init__(self, subscription: Any) -> None:
        self.subscription = subscription
        self.retrieves: list[str] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def retrieve(self, subscription_id: str) -> Any:
        self.retrieves.append(subscription_id)
        return self.subscription

    def update(self, subscription_id: str, params: dict[str, Any]) -> Any:
        self.updates.append((subscription_id, params))
        # Stripe re-anchors the period on an immediate upgrade; simulate that so
        # a result reading the NEW end would be visibly wrong.
        self.subscription.current_period_end = _NEW_PERIOD_END
        return self.subscription


class _RecordingSessions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        return SimpleNamespace(id="cs_should_not_happen")


class _RecordingSchedules:
    def __init__(self, end_date: int = _OLD_PERIOD_END) -> None:
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self._schedule = SimpleNamespace(
            id="sub_sched_1",
            phases=SimpleNamespace(
                data=[
                    SimpleNamespace(
                        start_date=1_700_000_000,
                        end_date=end_date,
                        items=SimpleNamespace(
                            data=[SimpleNamespace(price="price_pro", quantity=1)]
                        ),
                    )
                ]
            ),
        )

    def create(self, params: dict[str, Any]) -> Any:
        self.creates.append(params)
        return self._schedule

    def retrieve(self, schedule_id: str) -> Any:
        return self._schedule

    def update(self, schedule_id: str, params: dict[str, Any]) -> Any:
        self.updates.append((schedule_id, params))
        return self._schedule


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_PRO_PLUS", "price_pro_plus")
    get_settings.cache_clear()

    service = StripeService()
    subscriptions = _RecordingSubscriptions(_subscription())
    sessions = _RecordingSessions()
    schedules = _RecordingSchedules()
    service._client = SimpleNamespace(
        v1=SimpleNamespace(
            subscriptions=subscriptions,
            checkout=SimpleNamespace(sessions=sessions),
            subscription_schedules=schedules,
        )
    )
    yield service, subscriptions, sessions, schedules
    get_settings.cache_clear()


def test_upgrade_uses_subscriptions_update_and_never_creates_a_checkout_session(
    stripe: Any,
) -> None:
    service, subscriptions, sessions, _ = stripe

    result = asyncio.run(
        service.change_subscription_plan(
            "sub_1",
            new_price_id="price_pro_plus",
            user_id="7",
            tier="pro_plus",
            is_upgrade=True,
        )
    )

    assert result is not None
    assert result.is_upgrade is True
    assert len(subscriptions.updates) == 1
    sub_id, params = subscriptions.updates[0]
    assert sub_id == "sub_1"
    assert params["items"] == [{"id": "si_1", "price": "price_pro_plus"}]
    assert params["proration_behavior"] == "always_invoice"
    assert params["metadata"]["tier"] == "pro_plus"
    assert params["metadata"]["user_id"] == "7"
    # The whole point: no new subscription is ever created.
    assert sessions.calls == []


def test_upgrade_reports_the_old_period_end_captured_before_the_update(
    stripe: Any,
) -> None:
    service, subscriptions, _, _ = stripe

    result = asyncio.run(
        service.change_subscription_plan(
            "sub_1",
            new_price_id="price_pro_plus",
            user_id="7",
            tier="pro_plus",
            is_upgrade=True,
        )
    )

    assert result is not None
    assert result.previous_period_end == datetime.fromtimestamp(_OLD_PERIOD_END, tz=UTC)
    # The update did move the real period end; the result must not have used it.
    assert subscriptions.subscription.current_period_end == _NEW_PERIOD_END


def test_downgrade_is_scheduled_and_does_not_change_the_price_now(stripe: Any) -> None:
    service, subscriptions, sessions, schedules = stripe

    result = asyncio.run(
        service.change_subscription_plan(
            "sub_1",
            new_price_id="price_pro",
            user_id="7",
            tier="pro",
            is_upgrade=False,
        )
    )

    assert result is not None
    assert result.is_upgrade is False
    assert result.scheduled_effective_at == datetime.fromtimestamp(_OLD_PERIOD_END, tz=UTC)
    # No immediate price change, and definitely no new subscription.
    assert subscriptions.updates == []
    assert sessions.calls == []
    assert len(schedules.creates) == 1
    assert schedules.creates[0] == {"from_subscription": "sub_1"}
    assert len(schedules.updates) == 1
    _, params = schedules.updates[0]
    assert params["proration_behavior"] == "none"
    assert params["end_behavior"] == "release"
    # The future phase carries the new tier so the webhook can activate it.
    assert params["phases"][1]["items"] == [{"price": "price_pro", "quantity": 1}]
    assert params["phases"][1]["metadata"]["tier"] == "pro"


def test_change_plan_returns_none_when_stripe_is_not_configured() -> None:
    # Pydantic settings also read a developer's .env, so ``delenv`` is not
    # enough to make Stripe "unconfigured"; force the flag directly.
    service = StripeService()
    service._enabled = False

    result = asyncio.run(
        service.change_subscription_plan(
            "sub_1",
            new_price_id="price_pro_plus",
            user_id="7",
            tier="pro_plus",
            is_upgrade=True,
        )
    )
    assert result is None
