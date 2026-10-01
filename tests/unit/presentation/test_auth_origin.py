"""The request-origin dependency: an ``X-API-Key`` header means ``API``.

``get_request_origin`` reports how a request authenticated without re-running
authentication. It must mirror the precedence inside ``get_current_user`` (an
API key wins over the bearer token, because that is the credential the API
actually uses) and must never reject a request on its own.
"""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.conversions.value_object.job_origin import JobOrigin
from src.presentation.api.dependencies.auth_dependencies import (
    RequestOrigin,
    get_request_origin,
)


def test_api_key_header_reports_api() -> None:
    assert asyncio.run(get_request_origin("tr_abc123")) is JobOrigin.API


def test_absent_api_key_header_reports_web() -> None:
    assert asyncio.run(get_request_origin(None)) is JobOrigin.WEB


def test_blank_api_key_header_reports_web() -> None:
    """An empty header is not a credential, and ``get_current_user`` ignores it too."""
    assert asyncio.run(get_request_origin("")) is JobOrigin.WEB


def _origin_client() -> TestClient:
    """A throwaway app whose only dependency is the ``RequestOrigin`` alias.

    This exercises the alias through FastAPI's dependency resolution, which the
    direct calls above cannot — a broken ``Depends`` wiring would only show up
    here.
    """
    app = FastAPI()

    @app.get("/origin")
    async def _origin(origin: RequestOrigin) -> dict[str, str]:
        return {"origin": origin.value}

    return TestClient(app)


def test_alias_resolves_to_api_when_the_header_is_present() -> None:
    response = _origin_client().get("/origin", headers={"X-API-Key": "tr_live"})

    assert response.status_code == 200
    assert response.json() == {"origin": "API"}


def test_alias_resolves_to_web_when_the_header_is_absent() -> None:
    response = _origin_client().get("/origin")

    assert response.status_code == 200
    assert response.json() == {"origin": "WEB"}
