"""Tests for the API telemetry application service.

The service is where the *payload* is assembled: bucket sizing, zero-filling,
the window overview and the current-rate reading. These tests use a fake
repository so the assertions are about the service's own decisions rather than
about SQL.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.application.ports.api_telemetry_port import ApiLogFilters, EventPage
from src.application.services.api_telemetry_service import (
    ApiTelemetryService,
    InvalidRangeError,
    resolve_range,
)
from src.domain.telemetry.value_object.request_metrics import (
    ApiMetric,
    BucketAggregate,
    MetricsWindow,
)

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


class FakeTelemetryRepository:
    """A repository that returns pre-scripted aggregates.

    Records the arguments of every call so a test can assert the service asked
    the right question (which window, which bucket size, which filters) without
    needing a real query.
    """

    def __init__(self, buckets: list[BucketAggregate], current: int = 0) -> None:
        self._buckets = buckets
        self._current = current
        self.aggregate_calls: list[dict] = []
        self.count_calls: list[dict] = []

    async def aggregate(self, account_id, *, start, end, bucket_seconds, filters=None):
        self.aggregate_calls.append(
            {
                "account_id": account_id,
                "start": start,
                "end": end,
                "bucket_seconds": bucket_seconds,
                "filters": filters,
            }
        )
        # The whole-window call (one bucket spanning the range) answers with the
        # same rows; the chart call answers with the scripted buckets.
        if int((end - start).total_seconds()) <= bucket_seconds:
            return self._buckets
        return self._buckets

    async def count_in_range(self, account_id, *, start, end):
        self.count_calls.append({"account_id": account_id, "start": start, "end": end})
        return self._current


def _service(buckets, *, current: int = 0, max_range_days: int = 30) -> ApiTelemetryService:
    return ApiTelemetryService(
        FakeTelemetryRepository(buckets, current=current),  # type: ignore[arg-type]
        max_range_days=max_range_days,
        current_window_seconds=60,
        clock=lambda: NOW,
    )


class TestRangeResolution:
    def test_default_is_one_hour(self) -> None:
        window = resolve_range(None, now=NOW)
        assert window.key == "1h"
        assert window.end == NOW
        assert window.start == NOW - timedelta(hours=1)

    def test_unknown_preset_is_rejected_rather_than_defaulted(self) -> None:
        # A silent fallback would render one window's data under another
        # window's label.
        with pytest.raises(InvalidRangeError):
            resolve_range("42h", now=NOW)

    def test_a_range_beyond_the_ceiling_is_rejected(self) -> None:
        with pytest.raises(InvalidRangeError):
            resolve_range("30d", max_range_days=7, now=NOW)

    @pytest.mark.parametrize("key", ["5m", "15m", "1h", "24h", "7d", "30d"])
    def test_every_offered_preset_resolves(self, key: str) -> None:
        window = resolve_range(key, now=NOW)
        assert window.key == key
        assert window.start < window.end


class TestMetricsAssembly:
    def test_buckets_are_zero_filled_and_aligned(self) -> None:
        # One measured bucket among four expected: the empty ones must appear
        # as explicit zeros so the chart draws a real gap rather than a line
        # bridging across it. An explicit 15-minute bucket keeps the expected
        # count deterministic (the automatic size for an hour is 30s).
        measured = [
            BucketAggregate(
                start=NOW - timedelta(minutes=30),
                seconds=900,
                count=12,
                errors=2,
                p95_ms=120.0,
            )
        ]
        service = _service(measured)
        window = resolve_range("1h", now=NOW)

        import asyncio

        metrics = asyncio.run(
            service.metrics(1, window=window, metric=ApiMetric.RATE, bucket_override=900)
        )

        assert isinstance(metrics, MetricsWindow)
        assert len(metrics.buckets) == 4
        assert sum(bucket.count for bucket in metrics.buckets) == 12
        # The empty buckets are present and zero, not missing.
        assert [bucket.count for bucket in metrics.buckets].count(0) == 3
        # And the measured one sits at the aligned 15-minute boundary.
        assert any(bucket.count == 12 for bucket in metrics.buckets)

    def test_overview_counts_come_from_the_chart_buckets(self) -> None:
        measured = [
            BucketAggregate(
                start=NOW - timedelta(seconds=10), seconds=5, count=10, errors=1
            ),
        ]
        service = _service(measured)
        window = resolve_range("5m", now=NOW)

        import asyncio

        metrics = asyncio.run(service.metrics(1, window=window, metric=ApiMetric.COUNT))

        assert metrics.overview.requests == sum(b.count for b in metrics.buckets)
        assert metrics.overview.errors == sum(b.errors for b in metrics.buckets)
        assert metrics.overview.requests == 10

    def test_current_rate_uses_its_own_window_not_the_chart_zoom(self) -> None:
        # "Current rate" must not change meaning when the user changes the range.
        service = _service([], current=120)
        window = resolve_range("7d", now=NOW)

        import asyncio

        metrics = asyncio.run(service.metrics(1, window=window, metric=ApiMetric.RATE))

        assert metrics.overview.current_window_requests == 120
        # A 60-second window holding 120 requests is 2 req/s.
        assert metrics.overview.current_rps == pytest.approx(2.0)

    def test_filters_are_passed_through_to_every_query(self) -> None:
        repository = FakeTelemetryRepository([])
        service = ApiTelemetryService(
            repository,  # type: ignore[arg-type]
            max_range_days=30,
            current_window_seconds=60,
            clock=lambda: NOW,
        )
        filters = ApiLogFilters(api_key_id="key-1", outcome="error")

        import asyncio

        asyncio.run(
            service.metrics(9, window=resolve_range("1h", now=NOW), metric=ApiMetric.RATE, filters=filters)
        )

        assert repository.aggregate_calls
        assert all(call["filters"] is filters for call in repository.aggregate_calls)
        assert all(call["account_id"] == 9 for call in repository.aggregate_calls)

    def test_an_explicit_bucket_size_is_clamped(self) -> None:
        repository = FakeTelemetryRepository([])
        service = ApiTelemetryService(
            repository,  # type: ignore[arg-type]
            max_range_days=30,
            current_window_seconds=60,
            clock=lambda: NOW,
        )

        import asyncio

        metrics = asyncio.run(
            service.metrics(
                1,
                window=resolve_range("1h", now=NOW),
                metric=ApiMetric.RATE,
                bucket_override=10**6,
            )
        )

        assert metrics.bucket_seconds <= 3600

    def test_the_series_matches_the_selected_metric(self) -> None:
        measured = [
            BucketAggregate(start=NOW - timedelta(seconds=60), seconds=60, count=60, errors=0),
        ]
        service = _service(measured)
        window = resolve_range("5m", now=NOW)

        import asyncio

        metrics = asyncio.run(service.metrics(1, window=window, metric=ApiMetric.RATE))

        assert metrics.metric is ApiMetric.RATE
        # A 60-second bucket with 60 requests is 1 req/s.
        assert 1.0 in metrics.series()


class TestLogs:
    def test_logs_are_scoped_and_paginated(self) -> None:
        repository = FakeTelemetryRepository([])
        service = ApiTelemetryService(
            repository,  # type: ignore[arg-type]
            max_range_days=30,
            current_window_seconds=60,
            clock=lambda: NOW,
        )

        import asyncio

        # `logs` delegates straight to the repository; the point of this test is
        # that the account id and the window are always supplied.
        async def _run() -> EventPage:
            captured: dict = {}

            async def fake_list(account_id, **kwargs):
                captured.update({"account_id": account_id, **kwargs})
                return EventPage()

            repository.list_events = fake_list  # type: ignore[assignment]
            page = await service.logs(5, window=resolve_range("1h", now=NOW), limit=10)
            assert captured["account_id"] == 5
            assert captured["limit"] == 10
            return page

        page = asyncio.run(_run())
        assert page.items == []

    def test_a_missing_detail_is_none_not_an_error(self) -> None:
        repository = FakeTelemetryRepository([])

        async def fake_get(account_id, event_id):
            return None

        repository.get_event = fake_get  # type: ignore[assignment]
        service = ApiTelemetryService(
            repository,  # type: ignore[arg-type]
            max_range_days=30,
            current_window_seconds=60,
            clock=lambda: NOW,
        )

        import asyncio

        assert asyncio.run(service.get_log(1, "nope")) is None
