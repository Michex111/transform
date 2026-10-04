"""Applying a Stripe promotion code to a subscription Checkout Session.

The feature is "a free month of Pro": an operator-created 100%-off coupon with
a promotion code on top, which this code path merely *applies*. Two things have
to be right or it fails silently:

1. ``checkout.sessions.create`` needs
   ``discounts: [{"promotion_code": "promo_…"}]`` — the **id**, not the code a
   student typed. :meth:`StripeService.resolve_promotion_code` performs that
   lookup. The double here returns ``promotion_codes.list``'s real
   ``ListObject`` **envelope** (``{data: [...]}``) and the service reads it with
   ``_stripe_list``; reading ``.data`` off a plain list is precisely the shape
   bug that once broke the downgrade path, so the envelope is load-bearing in
   these tests.

2. ``payment_method_collection: "if_required"`` is what lets Stripe skip card
   collection when the total due is 0. Without it a 100%-off promo still
   demands a card, i.e. it is not actually free.

Container shapes mirror the SDK, not a convenient uniform double: a Checkout
Session's ``discounts`` is a **plain list** (``Session.discounts`` is declared
``Optional[List[Discount]]``), while the promotion-code list is an envelope.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.adapters.payment.stripe_service import (
    ResolvedPromotion,
    StripeService,
)
from src.infrastructure.config.settings import get_settings


def _session(
    *,
    amount_total: int | None = 0,
    currency: str | None = "usd",
    discounts: Any = None,
) -> Any:
    return SimpleNamespace(
        id="cs_promo",
        url="https://checkout.stripe.com/c/pay/cs_promo",
        client_secret="cs_promo_secret",
        amount_total=amount_total,
        currency=currency,
        discounts=discounts,
    )


def _expanded_discount(
    *, code: str = "STUDENT1", percent_off: float = 100.0, duration: str = "once"
) -> Any:
    """A discount with both ``promotion_code`` and ``coupon`` expanded."""
    coupon = SimpleNamespace(percent_off=percent_off, duration=duration)
    return SimpleNamespace(
        id="di_1",
        promotion_code=SimpleNamespace(id="promo_student1", code=code, coupon=coupon),
        coupon=coupon,
    )


def _lookup_match(
    *, code: str = "STUDENT1", percent_off: float = 100.0, duration: str = "once"
) -> Any:
    """What ``promotion_codes.list`` returns for a coupon-expanded lookup.

    The coupon is nested under ``promotion`` because that is where the real API
    puts it — a ``PromotionCode`` has **no** top-level ``coupon`` field in this
    API version (its keys are active, code, created, customer,
    customer_account, expires_at, id, livemode, max_redemptions, metadata,
    object, promotion, restrictions, times_redeemed). A fake that puts
    ``coupon`` at the top level is more generous than Stripe and cannot see the
    difference; this one was written from the live response.
    """
    return SimpleNamespace(
        id="promo_student1",
        code=code,
        active=True,
        promotion=SimpleNamespace(
            type="coupon",
            coupon=SimpleNamespace(percent_off=percent_off, duration=duration),
        ),
    )


def _promo(cid: str = "promo_student1", code: str | None = "STUDENT1") -> ResolvedPromotion:
    """A resolved promotion as the route would pass it into the service."""
    return ResolvedPromotion(id=cid, code=code, percent_off=100.0, duration="once")


class _RecordingPromotionCodes:
    def __init__(self, matches: list[Any]) -> None:
        self.matches = matches
        self.lookups: list[dict[str, Any]] = []

    def list(self, params: dict[str, Any]) -> Any:
        self.lookups.append(params)
        # The real shape: `promotion_codes.list` returns a `ListObject`
        # envelope, NOT a bare list.
        return SimpleNamespace(data=self.matches)


class _RecordingSessions:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.calls: list[dict[str, Any]] = []

    def create(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        return self.session


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_PRO_PLUS", "price_pro_plus")
    # A developer's .env must not change which UI mode these cases exercise.
    monkeypatch.delenv("STRIPE_CHECKOUT_UI_MODE", raising=False)
    get_settings.cache_clear()

    service = StripeService()
    promotion_codes = _RecordingPromotionCodes([_lookup_match()])
    sessions = _RecordingSessions(_session())
    service._client = SimpleNamespace(
        v1=SimpleNamespace(
            checkout=SimpleNamespace(sessions=sessions),
            promotion_codes=promotion_codes,
        )
    )
    yield service, promotion_codes, sessions
    get_settings.cache_clear()


def _create(service: StripeService, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "user_id": "7",
        "email": "student@example.com",
        "tier": "pro",
        "success_url": "https://example.com/success",
        "cancel_url": "https://example.com/cancel",
    }
    kwargs.update(overrides)
    return asyncio.run(service.create_checkout_session(**kwargs))


# ---------------------------------------------------------------------------
# Resolving the code
# ---------------------------------------------------------------------------


def test_a_valid_code_resolves_to_its_promotion_code_id(stripe: Any) -> None:
    service, promotion_codes, _ = stripe

    resolved = asyncio.run(service.resolve_promotion_code("STUDENT1"))

    assert resolved is not None
    assert resolved.id == "promo_student1"
    # The coupon's facts come back from THIS lookup, not from the session —
    # a resolved Discount carries no percent_off/duration at all.
    assert resolved.percent_off == 100.0
    assert resolved.duration == "once"
    # Case-insensitive match and unique-among-active is Stripe's contract; what
    # we must send is the code, active-only, single result — and the coupon
    # expansion, without which the facts above are unreadable. The path is
    # `data.promotion.coupon`: `data.coupon` is accepted by Stripe and expands
    # NOTHING, which is a silent failure, so this string is pinned.
    assert promotion_codes.lookups == [
        {
            "code": "STUDENT1",
            "active": True,
            "limit": 1,
            "expand": ["data.promotion.coupon"],
        }
    ]


def test_the_coupon_is_read_from_the_promotion_object(stripe: Any) -> None:
    """The coupon lives at ``promotion.coupon``, NOT at ``coupon``.

    Regression guard: an earlier version read ``PromotionCode.coupon``, which
    does not exist in this API version, so ``percent_off``/``duration`` came
    back ``None`` against real Stripe while this suite stayed green. The fake
    now mirrors the live object, so reading the wrong field fails here.
    """
    service, _, _ = stripe

    resolved = asyncio.run(service.resolve_promotion_code("STUDENT1"))

    assert resolved is not None
    assert resolved.percent_off == 100.0
    assert resolved.duration == "once"


def test_a_coupon_left_as_a_bare_id_degrades_instead_of_raising(stripe: Any) -> None:
    """If the expansion does not land, the id must still resolve.

    ``promotion.coupon`` is then a plain ``coupon_…`` string, so there is no
    percentage to report — but a usable promotion id is still returned, and the
    caller must not be handed an exception. That is the difference between a
    discount with no summary and a checkout that cannot start.
    """
    service, promotion_codes, _ = stripe
    promotion_codes.matches = [
        SimpleNamespace(
            id="promo_student1",
            code="STUDENT1",
            active=True,
            promotion=SimpleNamespace(type="coupon", coupon="campus-free-month"),
        )
    ]

    resolved = asyncio.run(service.resolve_promotion_code("STUDENT1"))

    assert resolved is not None
    assert resolved.id == "promo_student1"
    assert resolved.code == "STUDENT1"
    assert resolved.percent_off is None
    assert resolved.duration is None


def test_a_blank_code_is_never_looked_up(stripe: Any) -> None:
    """Blank means absent, not invalid: no request, no error."""
    service, promotion_codes, _ = stripe

    assert asyncio.run(service.resolve_promotion_code("   ")) is None
    assert promotion_codes.lookups == []


def test_an_empty_list_envelope_reports_no_match(stripe: Any) -> None:
    """Regression guard for ``_stripe_list``: the real no-result shape is an
    empty ``{data: []}`` envelope. Falling back to ``.data`` on a plain list, or
    assuming a bare list, is what previously made the downgrade path see an
    empty schedule."""
    service, promotion_codes, _ = stripe
    promotion_codes.matches = []

    assert asyncio.run(service.resolve_promotion_code("NOPE")) is None
    # It did perform the lookup; it just found nothing.
    assert len(promotion_codes.lookups) == 1


# ---------------------------------------------------------------------------
# Session parameters
# ---------------------------------------------------------------------------


def test_a_valid_code_is_sent_as_the_resolved_promotion_code_id(stripe: Any) -> None:
    service, _, sessions = stripe

    resolved = asyncio.run(service.resolve_promotion_code("STUDENT1"))
    handle = _create(service, promotion=resolved)

    assert handle is not None
    params = sessions.calls[0]
    # The `promo_…` id, NOT the "STUDENT1" string the student typed.
    assert params["discounts"] == [{"promotion_code": "promo_student1"}]
    # The whole payload, minus the deliberately-random integration identifier.
    assert {k: v for k, v in params.items() if k != "integration_identifier"} == {
        "line_items": [{"price": "price_pro", "quantity": 1}],
        "mode": "subscription",
        "metadata": {"user_id": "7", "tier": "pro", "kind": "subscription"},
        "subscription_data": {
            "metadata": {"user_id": "7", "tier": "pro", "kind": "subscription"}
        },
        "payment_method_collection": "if_required",
        "discounts": [{"promotion_code": "promo_student1"}],
        "customer_email": "student@example.com",
        "success_url": "https://example.com/success",
        "cancel_url": "https://example.com/cancel",
    }
    assert str(params["integration_identifier"]).startswith("transform_")


def test_payment_method_collection_is_if_required_with_a_promo(stripe: Any) -> None:
    """The crux: with the total due at 0, Stripe skips card collection — which
    is what makes a 100%-off promo a genuinely card-free free month."""
    service, _, sessions = stripe

    _create(service, promotion=_promo())

    assert sessions.calls[0]["payment_method_collection"] == "if_required"


def test_payment_method_collection_is_if_required_without_a_promo(stripe: Any) -> None:
    """Set unconditionally: harmless at full price (a card is still collected
    because the total is non-zero), and no branch can forget it."""
    service, _, sessions = stripe

    _create(service)

    assert sessions.calls[0]["payment_method_collection"] == "if_required"
    assert "discounts" not in sessions.calls[0]


# ---------------------------------------------------------------------------
# Reading the applied discount back
# ---------------------------------------------------------------------------


def test_totals_and_discount_are_projected_from_the_session(stripe: Any) -> None:
    service, _, sessions = stripe
    sessions.session = _session(
        amount_total=0,
        currency="usd",
        discounts=[_expanded_discount()],
    )

    handle = _create(service, promotion=_promo())

    assert handle is not None
    assert handle.amount_total == 0
    assert handle.currency == "usd"
    assert handle.discount_code == "STUDENT1"
    assert handle.discount_percent_off == 100.0
    assert handle.discount_duration == "once"


def test_a_discount_on_the_singular_field_is_also_read(stripe: Any) -> None:
    """Older API versions expose ``discount`` (singular) rather than
    ``discounts``; both must work. The session's own value wins when it is
    present, so a future Stripe that really does expand the discount is
    believed over what we cached from the lookup."""
    service, _, sessions = stripe
    session = _session(amount_total=0, currency="usd")
    session.discount = _expanded_discount(code="LEGACY")
    sessions.session = session

    handle = _create(service, promotion=_promo("promo_legacy", code="PROMO"))

    assert handle is not None
    assert handle.discount_code == "LEGACY"


@pytest.mark.parametrize("discounts", [None, [], ["dis_bare_id"]])
def test_a_session_without_a_usable_discount_yields_nones(
    stripe: Any, discounts: Any
) -> None:
    """No discount, or an id-only one, must degrade to Nones — never raise.

    No promotion is passed, so there is nothing to fall back to: this is the
    genuinely-no-discount case."""
    service, _, sessions = stripe
    sessions.session = _session(amount_total=999, currency="usd", discounts=discounts)

    handle = _create(service)

    assert handle is not None
    assert handle.discount_code is None
    assert handle.discount_percent_off is None
    assert handle.discount_duration is None


def test_the_lookup_supplies_the_facts_when_the_session_does_not_expand_them(
    stripe: Any,
) -> None:
    """THE regression guard for this feature, and the shape that really occurs.

    A resolved ``Discount`` carries no ``percent_off`` and no ``duration``, and
    its ``promotion_code`` is a bare ``promo_…`` id unless Stripe was asked to
    expand it — which we never ask for on the session. An earlier version read
    the coupon off the session only, so every one of these fields came back
    ``None`` against real Stripe while the suite (whose fake returned an
    *expanded* discount) stayed green. The SPA then rendered no discount block
    at all: a silently dead feature that looked perfectly healthy.

    Here the session reports the un-expanded shape and the facts must still be
    reported, because they came from the promotion lookup.
    """
    service, _, sessions = stripe
    sessions.session = _session(
        amount_total=0,
        currency="usd",
        # Exactly what the API returns by default: the promotion code as an id.
        discounts=[SimpleNamespace(id="di_1", promotion_code="promo_student1")],
    )

    handle = _create(service, promotion=_promo())

    assert handle is not None
    assert handle.amount_total == 0
    assert handle.currency == "usd"
    # The whole point: these are present even though the session could not say.
    assert handle.discount_code == "STUDENT1"
    assert handle.discount_percent_off == 100.0
    assert handle.discount_duration == "once"
