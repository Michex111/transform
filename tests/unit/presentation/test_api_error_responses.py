"""The canonical error payloads, and the CORS header an unhandled 500 needs.

The CORS half is not cosmetic. Starlette handles an unexpected exception in
``ServerErrorMiddleware``, which sits *outside* ``CORSMiddleware``; the browser
therefore never receives ``Access-Control-Allow-Origin`` on that response and
reports the whole thing as a network failure. That is why a 500 visible in the
request log reached the user as "failed to fetch" — the failing case is exactly
the one the user cannot describe, so these tests pin it precisely.
"""

import json

import pytest
from starlette.requests import Request

import src.presentation.api.main as api_main
from src.application.ports.llm_port import LlmUnavailableError
from src.infrastructure.adapters.ai import LlmRequestError
from src.presentation.api.error_responses import (
    AI_BUSY_CODE,
    AI_PROVIDER_ERROR_CODE,
    assistant_busy_response,
    assistant_provider_error_response,
    internal_error_response,
)

_ALLOWED = "https://app.example"


def _request(origin: str | None = None) -> Request:
    """A minimal request carrying just the header the handler reads.

    Building the scope by hand keeps this independent of HTTPServer/TestClient
    behaviour, which is what makes the allow-list assertions deterministic.
    """
    headers = [(b"origin", origin.encode())] if origin else []
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/assistant/summarize",
            "headers": headers,
            "query_string": b"",
            "scheme": "https",
        }
    )


def _detail(response) -> dict:
    return json.loads(response.body)["detail"]


class TestInternalErrorResponse:
    def test_it_is_a_json_500(self) -> None:
        response = internal_error_response(_request(), [_ALLOWED])
        assert response.status_code == 500
        assert _detail(response)["code"] == "INTERNAL_ERROR"

    def test_it_adds_cors_headers_for_an_allowed_origin(self) -> None:
        # Without these the browser throws the response away and the user is told
        # the network is down, for a bug on our side.
        response = internal_error_response(_request(_ALLOWED), [_ALLOWED])
        assert response.headers["access-control-allow-origin"] == _ALLOWED
        assert response.headers["access-control-allow-credentials"] == "true"
        # The body is the same for every origin but the headers are not, so a
        # shared cache must not reuse one origin's response for another.
        assert response.headers["vary"] == "Origin"

    def test_it_does_not_reflect_an_unlisted_origin(self) -> None:
        response = internal_error_response(_request("https://evil.example"), [_ALLOWED])
        assert "access-control-allow-origin" not in response.headers

    def test_it_adds_nothing_without_an_origin(self) -> None:
        # A server-to-server call is not a CORS request and needs no headers.
        response = internal_error_response(_request(), [_ALLOWED])
        assert "access-control-allow-origin" not in response.headers

    def test_it_never_leaks_the_exception(self) -> None:
        # The message is fixed: an unhandled exception's text can quote internals,
        # and this body is returned to the caller.
        detail = _detail(internal_error_response(_request(_ALLOWED), [_ALLOWED]))
        assert set(detail) == {"code", "message"}
        assert "error" not in detail["message"].lower() or "our side" in detail["message"]


class TestProviderFailureResponses:
    def test_busy_is_a_503_that_invites_a_retry(self) -> None:
        response = assistant_busy_response()
        assert response.status_code == 503
        assert _detail(response)["code"] == AI_BUSY_CODE

    def test_provider_error_is_a_502_that_does_not(self) -> None:
        # 502, not 503: the provider refused us, so "try again" would be advice
        # that cannot work and the fault is upstream configuration.
        response = assistant_provider_error_response()
        assert response.status_code == 502
        assert _detail(response)["code"] == AI_PROVIDER_ERROR_CODE

    def test_the_two_are_distinguishable(self) -> None:
        assert _detail(assistant_busy_response())["code"] != _detail(
            assistant_provider_error_response()
        )["code"]


class TestHandlerRegistration:
    """The mapping must be on the application, not repeated per endpoint.

    Registering these per endpoint is how `/chat` came to handle a provider
    failure while `/summarize` and `/recommend` answered the same condition with
    a 500. Asserting the registration fails a test if someone moves the logic
    back into an endpoint.
    """

    def test_registered_for_provider_failures(self) -> None:
        handlers = api_main.app.exception_handlers
        assert LlmUnavailableError in handlers
        assert LlmRequestError in handlers

    def test_registered_for_unhandled_errors(self) -> None:
        # This is the one that carries the CORS headers for a 500.
        assert Exception in api_main.app.exception_handlers


@pytest.mark.parametrize("handler_type", [LlmUnavailableError, LlmRequestError])
def test_provider_handlers_are_coroutines(handler_type: type[Exception]) -> None:
    """A sync handler would be run in a threadpool and could not return our response."""
    import inspect

    handler = api_main.app.exception_handlers[handler_type]
    assert inspect.iscoroutinefunction(handler)


# ---------------------------------------------------------------------------
# The mechanism, through a real middleware stack
# ---------------------------------------------------------------------------


def _app_with_a_raising_route(*, with_handler: bool):
    """A minimal app shaped like ours: CORS inside, an optional 500 handler.

    Built here rather than reusing the real app so the allow-list is explicit and
    the control case can omit the handler.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_ALLOWED],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    if with_handler:

        @app.exception_handler(Exception)
        async def _handle(request, exc):  # noqa: ANN001, ANN202
            return internal_error_response(request, [_ALLOWED])

    return app


def test_an_unhandled_error_is_readable_by_the_browser() -> None:
    """The fix, end to end: a 500 the SPA can actually display."""
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_a_raising_route(with_handler=True), raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": _ALLOWED})

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert response.headers.get("access-control-allow-origin") == _ALLOWED


def test_without_the_handler_a_500_loses_its_cors_header() -> None:
    """The control, pinning the production symptom this handler exists to fix.

    ``ServerErrorMiddleware`` sits outside ``CORSMiddleware``, so Starlette's own
    500 carries no CORS header and the browser reports the failure as a network
    error. This is the "failed to fetch" the report described — and it is why a
    request that was clearly a 500 in the log could not be diagnosed from the
    page. If this test ever starts failing, Starlette changed that ordering and
    the hand-added headers in ``internal_error_response`` may be redundant.
    """
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_a_raising_route(with_handler=False), raise_server_exceptions=False)
    response = client.get("/boom", headers={"Origin": _ALLOWED})

    assert response.status_code == 500
    assert "access-control-allow-origin" not in response.headers
