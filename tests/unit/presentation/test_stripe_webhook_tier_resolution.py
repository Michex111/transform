"""GAP 4 — tier resolution must survive missing schedule-phase metadata.

A scheduled downgrade carries the new tier only on the subscription schedule's
second phase. Stripe is documented to copy that metadata onto the subscription
at the phase boundary, but that cannot be verified from here; if it does not,
the webhook re-applies the OLD tier from ``metadata.tier``.
``_activate_subscription`` therefore falls back to the configured price id
before giving up, and raises ``ValueError`` only when neither source resolves.
"""

import asyncio
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_subscription_repo import (
    SQLSubscriptionRepository,
)
from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1 import webhooks

PRICES = SimpleNamespace(
    STRIPE_PRICE_PRO="price_pro",
    STRIPE_PRICE_PRO_PLUS="price_pro_plus",
    STRIPE_PRICE_ENTERPRISE="price_enterprise",
)


@contextmanager
def sqlite_session_factory():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture(name="prices")
def prices_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "get_settings", lambda: PRICES)


def test_price_id_resolves_the_tier_when_metadata_is_missing(prices: None) -> None:
    assert (
        webhooks._resolve_activation_tier(None, "price_pro_plus")
        is SubscriptionTier.PRO_PLUS
    )


def test_the_billed_price_wins_over_stale_metadata(prices: None) -> None:
    """The downgrade hazard, stated exactly.

    A downgrade puts the new tier on the subscription *schedule*'s second
    phase; Stripe copies that onto the subscription only at the phase
    boundary. If the copy does not happen, ``metadata.tier`` still names the
    OLD plan (``pro_plus``) while the item price is already the new one
    (``price_pro``). Metadata-first ordering would re-apply the old tier and the
    downgrade would never take effect, so the price must win.
    """
    assert (
        webhooks._resolve_activation_tier("pro_plus", "price_pro")
        is SubscriptionTier.PRO
    )


def test_metadata_is_used_when_the_price_is_unrecognised(prices: None) -> None:
    """An unconfigured or legacy price is "no information", not a guess.

    This is what keeps the checkout path working: a
    ``checkout.session.completed`` payload carries only a subscription *id*, so
    there is no price to read and the metadata written at checkout is all there
    is.
    """
    assert (
        webhooks._resolve_activation_tier("enterprise", "price_legacy_unknown")
        is SubscriptionTier.ENTERPRISE
    )
    assert (
        webhooks._resolve_activation_tier("pro_plus", None)
        is SubscriptionTier.PRO_PLUS
    )


def test_a_disagreement_is_logged(prices: None, caplog: pytest.LogCaptureFixture) -> None:
    """One of the two sources is wrong and which one is genuinely ambiguous,
    so the disagreement has to be visible rather than silently resolved."""
    with caplog.at_level(logging.WARNING):
        tier = webhooks._resolve_activation_tier("pro_plus", "price_pro")

    assert tier is SubscriptionTier.PRO
    assert any("disagree" in r.message.lower() for r in caplog.records)


def test_agreement_is_not_logged(prices: None, caplog: pytest.LogCaptureFixture) -> None:
    """The normal paths agree, so they must stay quiet."""
    with caplog.at_level(logging.WARNING):
        webhooks._resolve_activation_tier("pro", "price_pro")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_unresolvable_price_still_raises(prices: None) -> None:
    with pytest.raises(ValueError):
        webhooks._resolve_activation_tier(None, "price_unknown")
    with pytest.raises(ValueError):
        webhooks._resolve_activation_tier("not_a_tier", None)


def test_subscription_updated_applies_the_price_tier_when_metadata_is_stale(
    prices: None,
) -> None:
    """The downgrade hazard: metadata has no tier, the price names PRO_PLUS."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                event = {
                    "id": "sub_1",
                    "status": "active",
                    "customer": "cus_1",
                    "metadata": {"user_id": "42"},  # tier never copied across
                    "items": {"data": [{"price": {"id": "price_pro_plus"}}]},
                }
                await webhooks._handle_subscription_updated(session, event)

                row = await SQLSubscriptionRepository(session).get_wallet(42)
                assert row is not None
                assert row.tier == SubscriptionTier.PRO_PLUS

        asyncio.run(_run())


def test_first_price_id_tolerates_an_odd_payload(prices: None) -> None:
    assert webhooks._first_price_id({"items": {"data": []}}) is None
    assert webhooks._first_price_id({"items": {}}) is None
    assert webhooks._first_price_id({"items": {"data": [{"price": None}]}}) is None
    assert webhooks._first_price_id(None) is None
