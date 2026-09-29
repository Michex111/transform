"""The oversized-body guard.

Regression tests for `RequestBodyLimitMiddleware`: before it existed, any client
could declare an arbitrarily large body and make the server buffer it before a
single schema or dependency ran.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.presentation.api.middleware.body_limit import RequestBodyLimitMiddleware

_CAP = 1024


def _client(max_bytes: int = _CAP) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=max_bytes)

    @app.post("/api/echo")
    async def echo(request: Request) -> dict:  # pragma: no cover - trivial
        return {"read": len(await request.body())}

    @app.get("/health")
    async def health() -> dict:  # pragma: no cover - trivial
        return {"status": "ok"}

    return TestClient(app)


def test_an_oversized_declared_body_is_refused_with_413() -> None:
    body = b"x" * (_CAP + 1)
    response = _client().post("/api/echo", content=body)
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "REQUEST_TOO_LARGE"


def test_a_body_at_the_cap_is_accepted() -> None:
    # The boundary is `>`, not `>=`, so a body exactly at the cap is legal.
    response = _client().post("/api/echo", content=b"x" * _CAP)
    assert response.status_code == 200


def test_a_normal_body_is_untouched() -> None:
    response = _client().post("/api/echo", content=b"hello")
    assert response.status_code == 200
    assert response.json() == {"read": 5}


def test_paths_outside_the_api_prefix_are_not_capped() -> None:
    """Health probes must never be refusable by this middleware.

    A platform health check that started failing would take the service down, so
    the cap is deliberately scoped to `/api/`.
    """
    client = _client(max_bytes=1)
    assert client.get("/health").status_code == 200
    # The same oversized request IS refused once it targets the API surface.
    assert client.post("/api/echo", content=b"xxxxxxxxxx").status_code == 413


def test_a_malformed_content_length_is_not_treated_as_oversized() -> None:
    """A header this middleware cannot parse must not become a guess.

    It declines to act and lets the server's own parser arbitrate, rather than
    inventing a rejection rule (or, worse, coercing garbage into a length that
    accidentally permits or forbids the wrong requests).
    """
    response = _client().post(
        "/api/echo",
        content=b"hello",
        headers={"Content-Length": "not-a-number"},
    )
    # Whatever the transport decides, it is not OUR 413.
    assert response.status_code != 413
