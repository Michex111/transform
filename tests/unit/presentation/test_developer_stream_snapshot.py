"""Unit test for the live-stream snapshot builder.

The endpoint's guard behaviour is covered in the integration suite, but the
*contents* of a live frame cannot be asserted there: reading an infinite
`text/event-stream` body through the in-process test transport blocks until the
connection closes. So the frame is built directly here — which is also the
better place to assert it, because the builder is where the per-tick session and
the aggregate live.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.ports.api_telemetry_port import ApiLogFilters
from src.domain.telemetry.entities.api_request_event import ApiRequestEvent
from src.domain.telemetry.value_object.request_metrics import ApiMetric
from src.infrastructure.adapters.repository.sql_api_telemetry_repo import (
    SQLTelemetryRepository,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import Base
from src.presentation.api.routers.v1.developer import _snapshot_payload


def test_snapshot_payload_is_a_complete_metrics_frame() -> None:
    async def _run() -> dict:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        now = datetime.now(UTC)
        async with factory() as session:
            await SQLTelemetryRepository(session).save_events(
                [
                    ApiRequestEvent(
                        id=f"e{index}",
                        account_id=1,
                        api_key_id=None,
                        request_id=f"r{index}",
                        timestamp=now - timedelta(minutes=index + 1),
                        method="GET",
                        route_template="/api/v1/jobs",
                        status_code=500 if index == 2 else 200,
                        duration_ms=10.0,
                    )
                    for index in range(3)
                ]
                # Another account's row must not appear in the frame.
                + [
                    ApiRequestEvent(
                        id="other",
                        account_id=2,
                        api_key_id=None,
                        request_id="r-other",
                        timestamp=now - timedelta(minutes=1),
                        method="GET",
                        route_template="/api/v1/jobs",
                        status_code=200,
                        duration_ms=1.0,
                    )
                ]
            )

        try:
            payload = await _snapshot_payload(
                1,
                "1h",
                ApiMetric.RATE,
                ApiLogFilters(),
                get_settings(),
                factory,
            )
            return json.loads(payload)
        finally:
            await engine.dispose()

    body = asyncio.run(_run())

    # Every field the chart needs, in one frame.
    assert body["range_key"] == "1h"
    assert body["metric"] == "rate"
    assert body["metric_unit"] == "req/s"
    assert body["bucket_seconds"] > 0
    assert body["generated_at"]
    assert body["overview"]["requests"] == 3
    assert body["overview"]["errors"] == 1
    assert len(body["buckets"]) == len(body["values"])
    assert sum(bucket["count"] for bucket in body["buckets"]) == 3


def test_an_empty_account_still_produces_a_renderable_frame() -> None:
    async def _run() -> dict:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            payload = await _snapshot_payload(
                99, "5m", ApiMetric.P95, ApiLogFilters(), get_settings(), factory
            )
            return json.loads(payload)
        finally:
            await engine.dispose()

    body = asyncio.run(_run())

    # A frame with no traffic is still valid — the client must be able to render
    # "no requests" rather than treat it as an error.
    assert body["overview"]["requests"] == 0
    assert body["metric"] == "p95"
    assert all(value is None for value in body["values"])
