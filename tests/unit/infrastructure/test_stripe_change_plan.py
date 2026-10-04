"""In-place plan changes: ``StripeService.change_subscription_plan``.

The hazard these tests exist for: ``create_checkout_session`` ALWAYS creates a
new subscription, so using it for an upgrade would bill a customer who already
has one for a second, concurrent subscription. A plan change must go through
``subscriptions.update`` on the existing id instead.

The second hazard is ordering: an immediate upgrade moves
``current_period_end``, so the OLD value must be captured *before* the update —
that value is the carryover's expiry.

The third hazard is **container shape**, and it is the one that reached
production. Stripe's SDK is not uniform: ``subscription.items`` is a
``ListObject`` (a ``{data: [...]}`` envelope) but a subscription *schedule*'s
``phases`` and a phase's ``items`` are plain Python lists. This double returns an
envelope for the former and plain lists for the latter, exactly as the SDK does,
because an earlier version returned an envelope for everything and therefore
could not see the downgrade path raising "schedule has no phases" against real
Stripe. For the same reason the phase item's ``price`` is exercised in both
shapes — a plain id string (what a schedule carries) and an expanded object
(what ``subscription.items`` carries).
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
    # ``items`` is an envelope here on purpose: ``subscriptions.retrieve`` really
    # returns a ``ListObject``, which is what ``_first_subscription_item_id``
    # targets. (A schedule's ``phases`` is a plain list — see below.)
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
    def __init__(self, end_date: int = _OLD_PERIOD_END, price: Any = "price_pro") -> None:
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        # ``phases`` and each phase's ``items`` are PLAIN LISTS, matching the
        # SDK. Returning ``SimpleNamespace(data=[...])`` here is what previously
        # hid a production failure: the service read ``.data`` off these lists,
        # got nothing, and raised "schedule has no phases".
        self._schedule = SimpleNamespace(
            id="sub_sched_1",
            phases=[
                SimpleNamespace(
                    start_date=1_700_000_000,
                    end_date=end_date,
                    items=[SimpleNamespace(price=price, quantity=1)],
                )
            ],
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


def test_downgrade_re_reads_the_schedule_phases_as_a_plain_list() -> None:
    """Regression: a schedule's ``phases`` is a list, NOT a ``{data: []}`` envelope.

    Reading ``.data`` off it produced an empty list and the downgrade died with
    "Stripe subscription schedule has no phases" — a 500 on every attempt to
    move down a tier, while this file's double, which returned an envelope for
    every container, kept the suite green.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_PRO_PLUS", "price_pro_plus")
    get_settings.cache_clear()
    try:
        service = StripeService()
        schedules = _RecordingSchedules()
        service._client = SimpleNamespace(
            v1=SimpleNamespace(
                subscriptions=_RecordingSubscriptions(_subscription()),
                checkout=SimpleNamespace(sessions=_RecordingSessions()),
                subscription_schedules=schedules,
            )
        )

        result = asyncio.run(
            service.change_subscription_plan(
                "sub_1",
                new_price_id="price_pro",
                user_id="7",
                tier="pro",
                is_upgrade=False,
            )
        )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert result is not None
    _, params = schedules.updates[0]
    # The current phase was re-declared with the subscription's real item — not
    # stripped to an empty list, which is what reading `.data` produced.
    assert params["phases"][0]["items"] == [{"price": "price_pro", "quantity": 1}]
    assert params["phases"][0]["start_date"] == 1_700_000_000
    assert params["phases"][0]["end_date"] == _OLD_PERIOD_END


def test_downgrade_sends_the_price_id_when_stripe_expands_the_price_object() -> None:
    """Regression: the phase item's price must go on the wire as an id string.

    ``SubscriptionScheduleUpdateParamsPhaseItem.price`` is declared ``str``
    ("The ID of the price object"). A schedule's phase happens to carry a plain
    id today, but the same field arrives **expanded** as a ``Price`` object on
    ``subscription.items`` — so passing whatever the object holds straight
    through would serialize the whole Price, read-only fields and all, and
    Stripe would reject the request.
    """
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_PRO_PLUS", "price_pro_plus")
    get_settings.cache_clear()
    try:
        service = StripeService()
        # `stripe`'s Price deserialises as an object with an ``id``; a
        # SimpleNamespace carries the same salient shape for this decision.
        expanded = SimpleNamespace(id="price_pro", object="price", active=True)
        schedules = _RecordingSchedules(price=expanded)
        service._client = SimpleNamespace(
            v1=SimpleNamespace(
                subscriptions=_RecordingSubscriptions(_subscription()),
                checkout=SimpleNamespace(sessions=_RecordingSessions()),
                subscription_schedules=schedules,
            )
        )

        asyncio.run(
            service.change_subscription_plan(
                "sub_1",
                new_price_id="price_pro",
                user_id="7",
                tier="pro",
                is_upgrade=False,
            )
        )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    _, params = schedules.updates[0]
    sent = params["phases"][0]["items"][0]["price"]
    assert sent == "price_pro"
    # Not the object: sending it would let the SDK encode every read-only field.
    assert not hasattr(sent, "id")
