"""Cross-origin (CORS) behaviour required by the separately-hosted SPA.

The React app is deployed as its own static site, so every request the browser
makes to this API is cross-origin. The API must therefore:

* echo an allow-listed SPA origin, with credentials,
* let the ``Authorization`` request header through the preflight (the SSE
  stream and every authed API call send it),
* permit the fetch-based SSE GET (``Accept: text/event-stream``),
* refuse to emit CORS headers for an origin that is not allow-listed.

``CORSMiddleware`` is configured with explicit origins plus
``allow_headers=["*"]`` / ``allow_methods=["*"]``, so these checks exercise the
same code path regardless of which concrete origin is configured.
"""

import src.presentation.api.main as api_main
from tests.integration.dependencies.api_overrides import create_test_client

# Allow-listed in tests/conftest.py *before* the app is imported, so this suite
# does not depend on the developer's gitignored .env. In production the
# static-site origin (https://transform-web.onrender.com) plays this role.
SPA_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "https://not-allowed.example.com"


def test_spa_origin_is_allow_listed() -> None:
    """Precondition: surface a misconfigured allow-list as an actionable failure.

    Without this the remaining tests fail with a bare ``KeyError``/400 that
    hides the actual cause (an origin that was never allow-listed).
    """
    assert SPA_ORIGIN in api_main.settings.ALLOWED_ORIGINS, (
        f"{SPA_ORIGIN} must be allow-listed for these cross-origin tests to be "
        f"meaningful (got {api_main.settings.ALLOWED_ORIGINS!r})"
    )


def test_preflight_allows_authorization_with_credentials() -> None:
    """Authed API + SSE calls send `Authorization`, which needs a preflight OK."""
    with create_test_client() as client:
        response = client.options(
            "/api/v1/files",
            headers={
                "Origin": SPA_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization, accept",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == SPA_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert "GET" in response.headers["access-control-allow-methods"]


def test_preflight_allows_direct_upload_method() -> None:
    """The SPA uploads with a presigned PUT, so PUT must survive the preflight."""
    with create_test_client() as client:
        response = client.options(
            "/api/v1/files/urls",
            headers={
                "Origin": SPA_ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_sse_get_is_allowed_cross_origin() -> None:
    """The progress stream is a fetch GET with `Accept: text/event-stream`."""
    with create_test_client() as client:
        response = client.get(
            "/health",
            headers={"Origin": SPA_ORIGIN, "Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == SPA_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_disallowed_origin_receives_no_cors_headers() -> None:
    """Security: origins outside the allow-list must not be able to call the API."""
    with create_test_client() as client:
        response = client.options(
            "/api/v1/files",
            headers={
                "Origin": DISALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
