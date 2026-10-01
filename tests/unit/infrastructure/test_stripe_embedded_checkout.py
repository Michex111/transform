"""Embedded ("branded") Checkout Session construction.

The two UI modes are **mutually exclusive at Stripe's API**: an embedded session
is rejected outright if it carries ``success_url``/``cancel_url``, and a hosted
session is the only one that may carry them. These tests pin that split, the
brand overlay applied to the embedded form, and the "exactly one of
``url``/``client_secret``" contract the SPA mounts Stripe.js with.

Everything asserted here is something Stripe would otherwise only tell us at
runtime, *after* a customer had already clicked Upgrade — which is the worst
possible place to discover that the session cannot be created.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.adapters.payment.stripe_service import (
    _BRAND_BACKGROUND,
    _BRAND_BODY_FONT,
    _BRAND_PRIMARY,
    CheckoutSessionHandle,
    CheckoutUiMode,
    StripeService,
)
from src.infrastructure.config.settings import get_settings

#: Stand-in for the object the SDK returns. `url` is only set on a hosted
#: session and `client_secret` only on an embedded one, exactly as Stripe does.
_SESSION = SimpleNamespace(
    id="cs_test_unit",
    url="https://checkout.stripe.com/c/pay/cs_test_unit",
    client_secret="cs_test_unit_secret",
)

_SUCCESS_URL = "https://transform-to.com/app/billing?checkout=success"
_CANCEL_URL = "https://transform-to.com/app/billing?checkout=cancelled"


class _RecordingSessions:
    """Captures the params Stripe would have received, and answers with a session."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        return _SESSION


class _FakeClient:
    """The narrow slice of ``StripeClient`` the service actually touches."""

    def __init__(self, sessions: _RecordingSessions) -> None:
        self.v1 = SimpleNamespace(checkout=SimpleNamespace(sessions=sessions))


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real ``StripeService`` whose SDK client is replaced by a recorder.

    Settings are pinned rather than inherited from the developer's ``.env``: the
    whole point of these cases is *which mode wins*, and a local
    ``STRIPE_CHECKOUT_UI_MODE`` must not be able to change the verdict.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("APP_BASE_URL", "https://transform-to.com")
    monkeypatch.delenv("STRIPE_CHECKOUT_UI_MODE", raising=False)
    monkeypatch.delenv("STRIPE_CHECKOUT_LOGO_URL", raising=False)
    get_settings.cache_clear()

    service = StripeService()
    sessions = _RecordingSessions()
    service._client = _FakeClient(sessions)

    yield service, sessions

    get_settings.cache_clear()


def _create_subscription(service: StripeService, **overrides: Any) -> CheckoutSessionHandle | None:
    kwargs: dict[str, Any] = {
        "user_id": "7",
        "email": "buyer@example.com",
        "tier": "pro",
        "success_url": _SUCCESS_URL,
        "cancel_url": _CANCEL_URL,
    }
    kwargs.update(overrides)
    return asyncio.run(service.create_checkout_session(**kwargs))


# ---------------------------------------------------------------------------
# Which mode is used
# ---------------------------------------------------------------------------


class TestResolveUiMode:
    def test_defaults_to_what_the_client_asked_for(self, stripe: Any) -> None:
        service, _ = stripe
        assert service._resolve_ui_mode("embedded") is CheckoutUiMode.EMBEDDED
        assert service._resolve_ui_mode("hosted") is CheckoutUiMode.HOSTED

    def test_an_unknown_request_falls_back_to_hosted(self, stripe: Any) -> None:
        # Degrading beats raising: the caller is the only thing that would be
        # broken by a typo, and the hosted page always works.
        service, _ = stripe
        assert service._resolve_ui_mode("embeded") is CheckoutUiMode.HOSTED
        assert service._resolve_ui_mode("") is CheckoutUiMode.HOSTED

    def test_casing_and_padding_are_tolerated(self, stripe: Any) -> None:
        service, _ = stripe
        assert service._resolve_ui_mode("  EMBEDDED  ") is CheckoutUiMode.EMBEDDED

    @pytest.mark.parametrize(
        ("configured", "requested", "expected"),
        [
            ("hosted", "embedded", CheckoutUiMode.HOSTED),
            ("embedded", "hosted", CheckoutUiMode.EMBEDDED),
        ],
    )
    def test_a_configured_mode_overrides_the_client(
        self,
        stripe: Any,
        monkeypatch: pytest.MonkeyPatch,
        configured: str,
        requested: str,
        expected: CheckoutUiMode,
    ) -> None:
        # This is the kill switch: an operator can force either mode by setting
        # one env var and restarting, with no frontend deploy.
        service, _ = stripe
        monkeypatch.setenv("STRIPE_CHECKOUT_UI_MODE", configured)
        get_settings.cache_clear()
        assert service._resolve_ui_mode(requested) is expected


# ---------------------------------------------------------------------------
# Session parameters
# ---------------------------------------------------------------------------


class TestEmbeddedSessionParams:
    def test_embedded_session_omits_the_urls_stripe_rejects(self, stripe: Any) -> None:
        service, sessions = stripe
        handle = _create_subscription(service, ui_mode="embedded")

        params = sessions.calls[0]
        assert params["ui_mode"] == "embedded_page"
        # `success_url` doubles as `return_url`, which is what keeps the SPA's
        # existing `?checkout=success` handling working in both modes.
        assert params["return_url"] == _SUCCESS_URL
        assert "success_url" not in params
        assert "cancel_url" not in params
        assert handle is not None
        assert handle.client_secret == _SESSION.client_secret
        assert handle.url is None

    def test_embedded_session_lands_on_the_same_redirect_as_hosted(self, stripe: Any) -> None:
        service, sessions = stripe
        _create_subscription(service, ui_mode="embedded")

        params = sessions.calls[0]
        # `always` means the customer ends up on the URL the hosted flow would
        # have used, so no success affordance has to be rewritten.
        assert params["redirect_on_completion"] == "always"
        assert params["origin_context"] == "web"

    def test_embedded_session_carries_the_brand_overlay(self, stripe: Any) -> None:
        service, sessions = stripe
        _create_subscription(service, ui_mode="embedded")

        branding = sessions.calls[0]["branding_settings"]
        # Mirrors web/src/index.css. A drift here is invisible until someone
        # looks at the payment page, so it is pinned.
        assert branding["background_color"] == "#121417"
        assert branding["button_color"] == "#5a6bff"
        assert branding["font_family"] == "inter"
        assert branding["border_style"] == "rounded"
        assert branding["logo"] == {
            "type": "url",
            "url": "https://transform-to.com/apple-touch-icon.png",
        }

    def test_hosted_session_is_byte_for_byte_unchanged(self, stripe: Any) -> None:
        # The default path must not regress: this is what every deployed SPA
        # still uses while the embedded page rolls out.
        service, sessions = stripe
        handle = _create_subscription(service)

        params = sessions.calls[0]
        assert params["success_url"] == _SUCCESS_URL
        assert params["cancel_url"] == _CANCEL_URL
        assert "ui_mode" not in params
        assert "return_url" not in params
        assert "branding_settings" not in params
        assert handle is not None
        assert handle.url == _SESSION.url
        assert handle.client_secret is None

    def test_credit_packs_support_the_embedded_mode(self, stripe: Any) -> None:
        service, sessions = stripe
        handle = asyncio.run(
            service.create_credit_purchase_session(
                user_id="7",
                email="buyer@example.com",
                credits=100,
                amount_usd=10.0,
                success_url=_SUCCESS_URL,
                cancel_url=_CANCEL_URL,
                ui_mode="embedded",
            )
        )

        params = sessions.calls[0]
        assert params["mode"] == "payment"
        assert params["ui_mode"] == "embedded_page"
        assert params["line_items"][0]["price_data"]["unit_amount"] == 1000
        assert "success_url" not in params
        assert handle is not None and handle.client_secret


# ---------------------------------------------------------------------------
# Logo resolution
# ---------------------------------------------------------------------------


class TestCheckoutLogo:
    def test_a_local_origin_drops_the_logo_entirely(
        self, stripe: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Stripe fetches the logo server-side, so a localhost URL would fail the
        # session creation itself rather than merely hiding an image.
        service, _ = stripe
        monkeypatch.setenv("APP_BASE_URL", "http://localhost:5173")
        get_settings.cache_clear()

        assert "logo" not in service._branding_settings()

    def test_an_explicit_url_wins_over_the_derived_one(
        self, stripe: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, _ = stripe
        monkeypatch.setenv("STRIPE_CHECKOUT_LOGO_URL", "https://cdn.example.com/logo.png")
        get_settings.cache_clear()

        assert service._branding_settings()["logo"] == {
            "type": "url",
            "url": "https://cdn.example.com/logo.png",
        }

    def test_a_trailing_slash_on_the_origin_does_not_double_up(self, stripe: Any, monkeypatch) -> None:
        service, _ = stripe
        monkeypatch.setenv("APP_BASE_URL", "https://transform-to.com/")
        get_settings.cache_clear()

        assert service._branding_settings()["logo"]["url"] == (
            "https://transform-to.com/apple-touch-icon.png"
        )


# ---------------------------------------------------------------------------
# The url/client_secret contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ui_mode", "expected"),
    [
        (CheckoutUiMode.HOSTED, CheckoutSessionHandle(url=_SESSION.url, client_secret=None)),
        (CheckoutUiMode.EMBEDDED, CheckoutSessionHandle(client_secret=_SESSION.client_secret)),
    ],
)
def test_exactly_one_credential_is_handed_to_the_browser(
    ui_mode: CheckoutUiMode, expected: CheckoutSessionHandle
) -> None:
    # A hosted session does carry a client secret, but it is useless once the
    # session is hosted — and a credential the browser has no use for is exactly
    # what ends up in a log line.
    assert StripeService._session_handle(_SESSION, ui_mode) == expected


# ---------------------------------------------------------------------------
# Brand drift
# ---------------------------------------------------------------------------

_DESIGN_TOKENS = (
    Path(__file__).resolve().parents[3] / "web" / "src" / "index.css"
)


def test_checkout_branding_matches_the_spa_design_tokens() -> None:
    """The embedded form must not drift from the app it is embedded in.

    `branding_settings` duplicates values that live in the SPA's `@theme` block,
    which is a cross-language copy with no compiler behind it: recolouring the
    app would silently leave the payment form on the old palette, and nobody
    would notice until a customer saw two different brands in one flow.

    Reading the stylesheet as text (the same technique `spa-route-stubs.test.ts`
    uses for `App.tsx`) turns that silent drift into a failing test.
    """
    css = _DESIGN_TOKENS.read_text(encoding="utf-8")

    def token(name: str) -> str:
        match = re.search(rf"--{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{3,8}})\s*;", css)
        assert match is not None, f"--{name} not found in {_DESIGN_TOKENS}"
        return match.group(1).lower()

    assert _BRAND_BACKGROUND == token("color-background")
    assert _BRAND_PRIMARY == token("color-primary")

    # Stripe's font list is fixed, so the SPA's body font must be one of its
    # entries; `inter` is asserted here rather than parsed because a font stack
    # like `"Inter", system-ui, sans-serif` has no single value to compare.
    assert '"Inter"' in css
    assert _BRAND_BODY_FONT == "inter"
