"""Tests for serving the built SPA frontend from FastAPI."""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

import src.presentation.api.main as api_main
from src.infrastructure.config.settings import get_settings


@contextmanager
def spa_client() -> Generator[TestClient, None, None]:
    async def no_op_initialize_database() -> None:
        return None

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.initialize_database = original_init


def _dist_exists() -> bool:
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    return (dist / "index.html").is_file()


@pytest.mark.skipif(not _dist_exists(), reason="frontend build not present")
def test_spa_root_serves_index_html() -> None:
    with spa_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<div id=\"root\">" in response.text


@pytest.mark.skipif(not _dist_exists(), reason="frontend build not present")
def test_spa_deep_link_returns_index_html() -> None:
    """React Router deep links must fall back to index.html."""
    with spa_client() as client:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "<div id=\"root\">" in response.text


@pytest.mark.skipif(not _dist_exists(), reason="frontend build not present")
def test_spa_assets_are_served() -> None:
    """The hashed asset files referenced by index.html must be reachable."""
    with spa_client() as client:
        index = client.get("/").text
        js = next((s for s in index.split('"') if s.startswith("/assets/index-") and s.endswith(".js")), None)
        assert js, "expected a hashed JS asset in index.html"
        asset = client.get(js)
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("text/javascript")


@pytest.mark.skipif(not _dist_exists(), reason="frontend build not present")
def test_api_and_health_are_not_swallowed_by_spa() -> None:
    with spa_client() as client:
        # Health and docs still answer as the backend.
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 200
        # Unknown API paths return JSON 404, not the SPA.
        response = client.get("/api/v1/definitely-not-a-route")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
