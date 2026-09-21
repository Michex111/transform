"""Tests for the Prometheus metric path label (SEC-7 / OBS-1).

The label must be the matched route *template*, never the raw request path, so
that scanner/404 traffic cannot grow the metric cardinality without bound and
job/file ids never leak into ``/metrics``.
"""

import asyncio
from types import SimpleNamespace

from prometheus_client import generate_latest
from starlette.requests import Request
from starlette.responses import Response

import src.presentation.api.main as api_main


def _request(path: str, route_path: str | None) -> Request:
    scope: dict = {"type": "http", "method": "GET", "path": path, "headers": []}
    if route_path is not None:
        scope["route"] = SimpleNamespace(path=route_path)
    return Request(scope)


def test_metric_path_uses_the_route_template() -> None:
    request = _request("/api/v1/files/abc-123-def", "/api/v1/files/{file_id}")
    assert api_main._metric_path(request) == "/api/v1/files/{file_id}"


def test_metric_path_is_unmatched_for_unrouted_requests() -> None:
    request = _request("/wp-admin/secret-scan-42", None)
    assert api_main._metric_path(request) == "unmatched"


def test_metrics_middleware_never_labels_the_raw_path() -> None:
    async def call_next(request: Request) -> Response:
        del request
        return Response(status_code=200)

    asyncio.run(
        api_main.metrics_middleware(
            _request("/api/v1/files/leak-marker-999", "/api/v1/files/{file_id}"),
            call_next,
        )
    )

    body = generate_latest().decode()
    assert 'path="/api/v1/files/{file_id}"' in body
    # The raw id must never become a label value.
    assert "leak-marker-999" not in body
