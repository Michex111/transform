"""Application service for API request observability.

Owns the two things the router must not decide for itself: **what a time range
means** (preset → window, with a hard ceiling) and **how the metrics payload is
assembled** (bucket sizing, zero-filling, the window overview and the "current
rate" reading). Keeping them here means the SSE stream and the one-shot metrics
endpoint build the identical payload from the identical code — a live chart that
differed from its own snapshot would be worse than no live mode.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.application.ports.api_telemetry_port import (
    ApiLogFilters,
    EventPage,
    TelemetryRepositoryPort,
)
from src.domain.telemetry.entities.api_request_event import ApiRequestEvent
from src.domain.telemetry.value_object.bucketing import (
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
)

#: Selectable windows, in seconds. A fixed ladder (rather than free-form
#: start/end from the client) keeps the server's work predictable and the UI's
#: labels honest: every preset has a name the chart can print, and none of them
#: can exceed the configured retention/query ceiling.
RANGE_PRESETS: dict[str, int] = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}

DEFAULT_RANGE = "1h"


@dataclass(frozen=True)
class ResolvedRange:
    """A validated window plus the preset name it came from."""

    start: datetime
    end: datetime
    key: str

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class InvalidRangeError(ValueError):
    """Raised for an unsupported preset or an out-of-bounds custom window."""


def resolve_range(
    key: str | None,
    *,
    max_range_days: int = 30,
    now: datetime | None = None,
) -> ResolvedRange:
    """Turn a preset name into a concrete window ending "now".

    A module-level function (rather than only a method) so a caller can
    validate a range without constructing a service — the SSE endpoint and the
    MCP endpoints need the window but build their repository per tick. The
    method below delegates here, so there is one rule.

    Refuses an unknown preset rather than falling back to the default: a silent
    fallback would render one window's data under another window's label, which
    is exactly the class of lie this feature must not tell.
    """
    chosen = (key or DEFAULT_RANGE).strip().lower()
    if chosen not in RANGE_PRESETS:
        raise InvalidRangeError(
            f"Unknown range {key!r}; expected one of {sorted(RANGE_PRESETS)}."
        )
    seconds = RANGE_PRESETS[chosen]
    if seconds > timedelta(days=max_range_days).total_seconds():
        raise InvalidRangeError(
            f"Range {chosen!r} exceeds the maximum of {max_range_days} days."
        )
    end = now or datetime.now(UTC)
    return ResolvedRange(start=end - timedelta(seconds=seconds), end=end, key=chosen)


class ApiTelemetryService:
    """Read models for the API Logs dashboard."""

    def __init__(
        self,
        repository: TelemetryRepositoryPort,
        *,
        max_range_days: int = 30,
        current_window_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._max_range = timedelta(days=max_range_days)
        self._current_window = timedelta(seconds=current_window_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Range resolution
    # ------------------------------------------------------------------

    def resolve_range(self, key: str | None) -> ResolvedRange:
        """Turn a preset name into a concrete window ending "now".

        Delegates to the module-level :func:`resolve_range` so the rule lives
        once, and applies this instance's configured ceiling and clock.
        """
        return resolve_range(
            key,
            max_range_days=self._max_range.days,
            now=self._clock(),
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def metrics(
        self,
        account_id: int,
        *,
        window: ResolvedRange,
        metric: ApiMetric,
        filters: ApiLogFilters | None = None,
        bucket_override: int | None = None,
    ) -> MetricsWindow:
        """Build the complete chart payload for one window."""
        bucket_seconds = (
            clamp_bucket_seconds(bucket_override)
            if bucket_override
            else bucket_seconds_for_span(window.seconds)
        )

        measured = await self._repository.aggregate(
            account_id,
            start=window.start,
            end=window.end,
            bucket_seconds=bucket_seconds,
            filters=filters,
        )
        measured_by_start = {bucket.start: bucket for bucket in measured}
        buckets = fill_buckets(
            bucket_starts(window.start, window.end, bucket_seconds),
            measured_by_start,
            bucket_seconds,
        )

        overview = await self._overview(
            account_id, window=window, filters=filters, buckets=buckets
        )

        return MetricsWindow(
            start=window.start,
            end=window.end,
            bucket_seconds=bucket_seconds,
            metric=metric,
            overview=overview,
            buckets=buckets,
            generated_at=self._clock(),
        )

    async def _overview(
        self,
        account_id: int,
        *,
        window: ResolvedRange,
        filters: ApiLogFilters | None,
        buckets: list[BucketAggregate],
    ) -> MetricsOverview:
        """Window totals, window percentiles and the current rate.

        Counts are summed from the (already loaded) chart buckets — free and
        exactly consistent with what the chart shows. The **percentiles** need
        the whole window's samples, which no per-bucket value can reconstruct,
        so they come from a second aggregation that deliberately puts the entire
        window in a single bucket. That is one extra indexed query, and it is
        what makes the summary's p95 the p95 of the window rather than an
        average of bucket p95s (which is not a percentile of anything).
        """
        requests = sum(bucket.count for bucket in buckets)
        errors = sum(bucket.errors for bucket in buckets)

        whole = await self._repository.aggregate(
            account_id,
            start=window.start,
            end=window.end,
            bucket_seconds=max(1, int(window.seconds)),
            filters=filters,
        )
        single = whole[0] if whole else BucketAggregate(start=window.start, seconds=1)

        now = self._clock()
        current_start = now - self._current_window
        current_requests = await self._repository.count_in_range(
            account_id, start=current_start, end=now
        )
        current_seconds = max(1.0, (now - current_start).total_seconds())

        return MetricsOverview(
            requests=requests,
            errors=errors,
            avg_latency_ms=single.avg_latency_ms,
            p50_ms=single.p50_ms,
            p95_ms=single.p95_ms,
            p99_ms=single.p99_ms,
            current_rps=current_requests / current_seconds,
            current_window_requests=current_requests,
        )

    # ------------------------------------------------------------------
    # Log explorer
    # ------------------------------------------------------------------

    async def logs(
        self,
        account_id: int,
        *,
        window: ResolvedRange,
        filters: ApiLogFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> EventPage:
        return await self._repository.list_events(
            account_id,
            start=window.start,
            end=window.end,
            filters=filters,
            cursor=cursor,
            limit=limit,
        )

    async def get_log(self, account_id: int, event_id: str) -> ApiRequestEvent | None:
        """One request's detail, or ``None`` if it is not this account's."""
        return await self._repository.get_event(account_id, event_id)
