"""Tests asserting the API does NOT serve the SPA.

The React app is hosted separately (a Render static site) and reaches the API
cross-origin, so the API must never return an HTML application shell or serve
frontend assets — regardless of whether a ``web/dist`` build happens to exist
on disk. These tests deliberately do **not** skip when ``web/dist`` is absent,
and one of them creates a real build to prove it is ignored anyway.
"""

from pathlib import Path
from typing import Iterator

import pytest

import src.presentation.api.main as api_main
from tests.integration.dependencies.api_overrides import create_test_client

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST = _REPO_ROOT / "web" / "dist"
_SPA_MARKER = '<div id="root">'


def _assert_json_404(response) -> None:
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert _SPA_MARKER not in response.text
    assert "<html" not in response.text.lower()


def test_root_does_not_serve_spa_shell() -> None:
    """``/`` is an unknown API route — it must not hand back ``index.html``."""
    with create_test_client() as client:
        response = client.get("/")

    _assert_json_404(response)


def test_spa_deep_links_are_not_served() -> None:
    """React Router deep links are the static site's job, not the API's."""
    with create_test_client() as client:
        for path in ("/app/dashboard", "/app/files", "/pricing", "/login"):
            _assert_json_404(client.get(path))


def test_spa_assets_are_not_served() -> None:
    """``/assets/*`` is not mounted; hashed bundles live on the static site."""
    with create_test_client() as client:
        _assert_json_404(client.get("/assets/index-abc123.js"))


def test_backend_paths_still_behave() -> None:
    """Dismounting the SPA must not change API/health/docs behaviour."""
    with create_test_client() as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        # /metrics is served by the backend, not swallowed by any catch-all.
        assert client.get("/metrics").status_code == 200
        # Unknown API paths keep returning JSON 404s.
        _assert_json_404(client.get("/api/v1/definitely-not-a-route"))


def test_spa_serving_helpers_are_gone() -> None:
    """Regression guard: the SPA mount hooks must not be reintroduced."""
    assert not hasattr(api_main, "mount_frontend")
    assert not hasattr(api_main, "_resolve_frontend_dist")


@pytest.fixture
def real_frontend_dist() -> Iterator[Path]:
    """Create a real ``web/dist/index.html``, preserving any existing build."""
    created_dir = False
    if not _FRONTEND_DIST.exists():
        _FRONTEND_DIST.mkdir(parents=True)
        created_dir = True

    index = _FRONTEND_DIST / "index.html"
    index_existed = index.exists()
    if not index_existed:
        index.write_text(f"<!doctype html><html><body>{_SPA_MARKER}SPA</div></body></html>")

    try:
        yield _FRONTEND_DIST
    finally:
        if not index_existed:
            index.unlink(missing_ok=True)
        if created_dir:
            try:
                _FRONTEND_DIST.rmdir()
            except OSError:
                pass


def test_existing_frontend_build_is_ignored(real_frontend_dist: Path) -> None:
    """Even with a valid ``web/dist/index.html`` on disk, the API serves no SPA."""
    assert (real_frontend_dist / "index.html").is_file()

    with create_test_client() as client:
        _assert_json_404(client.get("/"))
        _assert_json_404(client.get("/app/dashboard"))
        _assert_json_404(client.get("/assets/index-abc123.js"))
