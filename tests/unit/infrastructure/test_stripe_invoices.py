"""``StripeService.list_invoices`` — the read-only billing-history fetch.

Invoices power a convenience screen, so the contract that matters is
*non-failure*: Stripe being unconfigured, or the API call blowing up, must
yield an empty list rather than propagate. The one thing that must be exact is
the params dict handed to ``client.v1.invoices.list`` — this SDK takes a DICT
as the positional argument, not keyword arguments, and a wrong shape would only
surface in production.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.infrastructure.adapters.payment.stripe_service import StripeService
from src.infrastructure.config.settings import get_settings


class _RecordingInvoices:
    """Captures the params Stripe would have received, and answers with data."""

    def __init__(self, *, data: list[Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._data = data or []
        self._error = error

    def list(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeClient:
    """The narrow slice of ``StripeClient`` the service actually touches."""

    def __init__(self, invoices: _RecordingInvoices) -> None:
        self.v1 = SimpleNamespace(invoices=invoices)


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real ``StripeService`` whose SDK client is replaced by a recorder."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    get_settings.cache_clear()

    service = StripeService()
    recorder = _RecordingInvoices(
        data=[
            SimpleNamespace(
                id="in_1",
                number="INV-0001",
                status="paid",
                amount_paid=999,
                amount_due=0,
                currency="usd",
                created=1_750_000_000,
                period_start=1_749_000_000,
                period_end=1_751_000_000,
                invoice_pdf="https://pay.stripe.com/inv/in_1.pdf",
                hosted_invoice_url="https://pay.stripe.com/inv/in_1",
            )
        ]
    )
    service._client = _FakeClient(recorder)

    yield service, recorder

    get_settings.cache_clear()


def test_disabled_service_returns_no_invoices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    try:
        service = StripeService()
        assert asyncio.run(service.list_invoices("cus_1")) == []
    finally:
        get_settings.cache_clear()


def test_a_stripe_error_is_swallowed(stripe: Any) -> None:
    service, recorder = stripe
    recorder._error = RuntimeError("stripe is down")

    # Never raises: invoices are a read-only convenience.
    assert asyncio.run(service.list_invoices("cus_1")) == []
    assert recorder.calls == [{"customer": "cus_1", "limit": 12}]


def test_params_are_a_dict_with_customer_and_limit(stripe: Any) -> None:
    service, recorder = stripe

    result = asyncio.run(service.list_invoices("cus_42", limit=5))

    assert recorder.calls == [{"customer": "cus_42", "limit": 5}]
    assert len(result) == 1
    assert result[0]["id"] == "in_1"
    # Raw unix ints stay raw; the router owns the datetime conversion.
    assert result[0]["created"] == 1_750_000_000
    assert result[0]["period_start"] == 1_749_000_000
