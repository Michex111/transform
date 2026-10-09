"""Unit tests for the telemetry domain rules.

These cover the parts that decide what the dashboard *means*: what counts as an
error, how a bucket is sized, how a percentile is interpolated, and whether a
gap in the data stays a gap. They are pure, so they run without a database or a
server — which is the point of keeping the rules out of the repository.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.telemetry.value_object.bucketing import (
    MAX_BUCKETS,
    MAX_BUCKET_SECONDS,
    MIN_BUCKET_SECONDS,
    align_to_bucket,
    bucket_seconds_for_span,
    bucket_starts,
    clamp_bucket_seconds,
)
from src.domain.telemetry.value_object.request_metrics import (
    ApiMetric,
    BucketAggregate,
    MetricsOverview,
    MetricsWindow,
    fill_buckets,
    is_error_status,
    is_success_status,
    percentile,
)


class TestOutcomeClassification:
    """The success/error rule, stated once and asserted at its boundaries."""

    @pytest.mark.parametrize("status", [100, 200, 204, 301, 302, 399])
    def test_below_four_hundred_is_success(self, status: int) -> None:
        assert is_success_status(status)
        assert not is_error_status(status)

    @pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 503])
    def test_four_hundred_and_above_is_error(self, status: int) -> None:
        assert is_error_status(status)
        assert not is_success_status(status)

    def test_redirect_counts_as_success(self) -> None:
        # `/mcp` 307s to `/mcp/`. Counting that as a failure would make every
        # MCP-enabled account look broken.
        assert is_success_status(307)


class TestBucketSizing:
    def test_short_window_gets_fine_buckets(self) -> None:
        # 5 minutes: a 1-second bucket would be 300 points, over the target, so
        # the ladder steps up to a size that keeps the chart readable.
        assert bucket_seconds_for_span(5 * 60) <= 5

    def test_hour_window_stays_in_the_documented_band(self) -> None:
        # The spec's starting point for "last hour" is 10–60s.
        assert 10 <= bucket_seconds_for_span(60 * 60) <= 60

    def test_seven_days_is_capped_at_hourly(self) -> None:
        assert bucket_seconds_for_span(7 * 24 * 3600) == MAX_BUCKET_SECONDS

    def test_never_exceeds_the_bucket_ceiling(self) -> None:
        assert bucket_seconds_for_span(365 * 24 * 3600) == MAX_BUCKET_SECONDS

    def test_non_positive_span_falls_back_to_the_minimum(self) -> None:
        assert bucket_seconds_for_span(0) == MIN_BUCKET_SECONDS
        assert bucket_seconds_for_span(-5) == MIN_BUCKET_SECONDS

    def test_clamp_bounds_both_ends(self) -> None:
        assert clamp_bucket_seconds(0) == MIN_BUCKET_SECONDS
        assert clamp_bucket_seconds(10**9) == MAX_BUCKET_SECONDS


class TestBucketAlignment:
    def test_alignment_is_pinned_to_the_epoch(self) -> None:
        # 10:00:07 with a 10-second bucket must land on 10:00:00, not on the
        # query window's start: otherwise the same instant falls in a different
        # bucket depending on when the user looked.
        moment = datetime(2026, 10, 9, 10, 0, 7, tzinfo=UTC)
        assert align_to_bucket(moment, 10) == datetime(2026, 10, 9, 10, 0, 0, tzinfo=UTC)

    def test_alignment_refuses_a_naive_datetime(self) -> None:
        # A naive datetime would be silently read as UTC, which is how a
        # timezone bug becomes a wrong chart rather than an exception.
        with pytest.raises(ValueError):
            align_to_bucket(datetime(2026, 10, 9, 10, 0, 7), 10)

    def test_zero_bucket_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            align_to_bucket(datetime(2026, 10, 9, tzinfo=UTC), 0)

    def test_bucket_starts_are_contiguous_and_aligned(self) -> None:
        start = datetime(2026, 10, 9, 10, 0, 3, tzinfo=UTC)
        end = datetime(2026, 10, 9, 10, 1, 0, tzinfo=UTC)
        starts = bucket_starts(start, end, 10)
        assert starts[0] == datetime(2026, 10, 9, 10, 0, 0, tzinfo=UTC)
        for earlier, later in zip(starts, starts[1:]):
            assert later - earlier == timedelta(seconds=10)
        # The bucket beginning exactly at `end` is excluded: it belongs to the
        # *next* window.
        assert all(moment < end for moment in starts)

    def test_bucket_count_is_bounded(self) -> None:
        # A pathological range must not produce an unbounded payload.
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 1, tzinfo=UTC)
        assert len(bucket_starts(start, end, 1)) <= MAX_BUCKETS


class TestPercentile:
    def test_empty_sample_has_no_percentile(self) -> None:
        # None, not 0: a bucket with no requests has no latency, and 0 would
        # draw a spike to the floor.
        assert percentile([], 0.95) is None

    def test_single_value_is_its_own_percentile(self) -> None:
        assert percentile([42.0], 0.95) == 42.0

    def test_interpolates_between_neighbours(self) -> None:
        # Ten values, p95 by linear interpolation, matching numpy's default.
        values = [float(n) for n in range(1, 11)]
        assert percentile(values, 0.5) == pytest.approx(5.5)
        assert percentile(values, 0.95) == pytest.approx(9.55)

    def test_extremes_are_the_min_and_max(self) -> None:
        values = [3.0, 1.0, 2.0]
        assert percentile(values, 0.0) == 1.0
        assert percentile(values, 1.0) == 3.0

    def test_rejects_a_quantile_outside_zero_to_one(self) -> None:
        with pytest.raises(ValueError):
            percentile([1.0], 1.5)


class TestBucketAggregate:
    def test_rate_is_the_within_bucket_average(self) -> None:
        bucket = BucketAggregate(start=datetime(2026, 1, 1, tzinfo=UTC), seconds=60, count=120)
        assert bucket.rate == pytest.approx(2.0)

    def test_error_rate_is_a_percentage(self) -> None:
        bucket = BucketAggregate(
            start=datetime(2026, 1, 1, tzinfo=UTC), seconds=60, count=200, errors=5
        )
        assert bucket.error_rate == pytest.approx(2.5)
        assert bucket.success == 195

    def test_zero_count_has_no_rate_rather_than_a_division_error(self) -> None:
        bucket = BucketAggregate(start=datetime(2026, 1, 1, tzinfo=UTC), seconds=60)
        assert bucket.rate == 0.0
        assert bucket.error_rate == 0.0

    def test_success_never_goes_negative(self) -> None:
        bucket = BucketAggregate(
            start=datetime(2026, 1, 1, tzinfo=UTC), seconds=60, count=1, errors=5
        )
        assert bucket.success == 0


class TestFillBuckets:
    def test_missing_buckets_become_explicit_zeros(self) -> None:
        starts = [datetime(2026, 1, 1, 0, minute, tzinfo=UTC) for minute in range(3)]
        measured = {
            starts[1]: BucketAggregate(start=starts[1], seconds=60, count=7, errors=1),
        }
        filled = fill_buckets(starts, measured, 60)
        assert [bucket.count for bucket in filled] == [0, 7, 0]
        assert filled[0].seconds == 60

    def test_measured_buckets_are_preserved_by_identity(self) -> None:
        starts = [datetime(2026, 1, 1, tzinfo=UTC)]
        bucket = BucketAggregate(start=starts[0], seconds=60, count=3)
        assert fill_buckets(starts, {starts[0]: bucket}, 60)[0] is bucket


class TestMetricsWindowSeries:
    def _window(self, metric: ApiMetric) -> MetricsWindow:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        buckets = [
            BucketAggregate(start=start, seconds=60, count=60, errors=0, p95_ms=10.0),
            BucketAggregate(start=start, seconds=60, count=0, errors=0, p95_ms=None),
        ]
        return MetricsWindow(
            start=start,
            end=start + timedelta(minutes=2),
            bucket_seconds=60,
            metric=metric,
            overview=MetricsOverview(requests=60),
            buckets=buckets,
        )

    def test_rate_series_is_per_bucket(self) -> None:
        assert self._window(ApiMetric.RATE).series() == [1.0, 0.0]

    def test_latency_series_preserves_a_gap_as_null(self) -> None:
        # The chart must break the line, not draw a drop to zero.
        assert self._window(ApiMetric.P95).series() == [10.0, None]

    def test_count_series_is_the_raw_counts(self) -> None:
        assert self._window(ApiMetric.COUNT).series() == [60.0, 0.0]


class TestMetricsOverview:
    def test_rates_are_zero_for_no_requests(self) -> None:
        overview = MetricsOverview()
        assert overview.success_rate == 0.0
        assert overview.error_rate == 0.0

    def test_success_and_error_rates_complement(self) -> None:
        overview = MetricsOverview(requests=1000, errors=6)
        assert overview.success == 994
        assert overview.success_rate == pytest.approx(99.4)
        assert overview.error_rate == pytest.approx(0.6)
