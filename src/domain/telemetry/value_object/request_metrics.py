"""Metric definitions for the API Logs dashboard.

Every number the dashboard shows is defined here, once. The chart, the summary
cards and the tests all read the same functions, so "success rate" cannot mean
99.4% in the card and 99.1% in the chart.

Outcome semantics (documented because they are a product decision, not an
implementation detail):

* **A request is successful when its HTTP status is below 400.** 1xx, 2xx and
  3xx all count. A 3xx is included deliberately: for this API a redirect is a
  normal answer (``/mcp`` → ``/mcp/``), not a failure, and counting it as one
  would make MCP traffic look broken.
* **A request is an error when its status is 400 or above.** There is no third
  bucket. A request that never produced a status (the client disconnected mid
  response) is **not recorded at all** — the response was never sent, so there
  is no honest status to attribute, and inventing one would put fabricated
  failures in the user's log. Streaming endpoints are excluded from capture for
  the same reason (see the middleware).
* **Rate-limited (429) and auth-failed (401) requests are not attributed.**
  They are refused before an account is known, so they have no owner to log
  against. They surface in the platform's own metrics, not in a user's log.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

#: The first status code that counts as an error. Kept as a named constant so
#: the classification rule is stated once and can be referenced in the docs
#: without re-deriving it from the comparison.
FIRST_ERROR_STATUS = 400


def is_error_status(status_code: int) -> bool:
    """Whether ``status_code`` counts as an error for the dashboard."""
    return status_code >= FIRST_ERROR_STATUS


def is_success_status(status_code: int) -> bool:
    """Whether ``status_code`` counts as a success for the dashboard."""
    return status_code < FIRST_ERROR_STATUS


class ApiMetric(StrEnum):
    """A series the chart can display.

    Values are the wire names used by ``?metric=``. They are a closed set: an
    unknown value is rejected by the router rather than silently defaulted,
    because defaulting would have the chart label one series and plot another.
    """

    RATE = "rate"
    COUNT = "count"
    P50 = "p50"
    P95 = "p95"
    ERROR_RATE = "error_rate"


#: Human label and unit for each metric, used to build the chart's axis caption
#: and the summary cards. Kept beside the enum so a new metric cannot be added
#: without stating what it is measured in — a chart that plots a bare number
#: with no unit is the failure mode this prevents.
METRIC_META: dict[ApiMetric, tuple[str, str]] = {
    ApiMetric.RATE: ("Request rate", "req/s"),
    ApiMetric.COUNT: ("Requests", "requests"),
    ApiMetric.P50: ("p50 latency", "ms"),
    ApiMetric.P95: ("p95 latency", "ms"),
    ApiMetric.ERROR_RATE: ("Error rate", "%"),
}


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear-interpolated percentile of ``values`` (numpy's default method).

    Implemented here rather than pushed entirely into SQL because the test
    database (SQLite) has no ``percentile_cont``; the repository uses the SQL
    function on PostgreSQL and this function everywhere else, and this is the
    reference the SQL path is asserted against. Interpolation (rather than
    "nearest rank") is what makes a p95 of a small sample stable: with ten
    observations, nearest-rank p95 is just the maximum and jumps around, while
    interpolation moves smoothly.

    Returns ``None`` for an empty sequence: there is no latency of no requests,
    and returning 0 would draw a spike to the floor on the chart.
    """
    if not values:
        return None
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower]) + (float(ordered[upper]) - float(ordered[lower])) * fraction


@dataclass
class BucketAggregate:
    """One time bucket's raw counts, before derived metrics are computed.

    Counts come from SQL; the percentile fields are filled by whichever path the
    repository used (the SQL function or :func:`percentile`). Derived values
    (rate, success rate) are computed on demand by the properties below so they
    can never drift from the counts they are built from.
    """

    start: datetime
    seconds: int
    count: int = 0
    errors: int = 0
    avg_latency_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None

    @property
    def success(self) -> int:
        """Requests that did not error. Never negative, even if a bad row
        somehow reported more errors than requests."""
        return max(0, self.count - self.errors)

    @property
    def rate(self) -> float:
        """Average requests per second *within this bucket*.

        This is a bucket average, not an instantaneous reading: over a 60-second
        bucket it is "requests / 60", which is why the chart labels it req/s and
        states the interval alongside it. Presenting it as an instantaneous
        value would be a lie the data cannot support.
        """
        return self.count / self.seconds if self.seconds > 0 else 0.0

    @property
    def error_rate(self) -> float:
        """Percentage of requests in this bucket that errored (0–100)."""
        return (self.errors / self.count * 100.0) if self.count else 0.0


@dataclass
class MetricsOverview:
    """Window-level totals plus the two "right now" readings.

    ``current_rps`` is measured over a fixed recent window (60s) rather than
    taken from the last chart bucket, so it does not change meaning when the
    user picks a different time range — a "current rate" that depends on the
    chart's zoom level is not a current rate.
    """

    requests: int = 0
    errors: int = 0
    avg_latency_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    current_rps: float = 0.0
    #: How many requests the last 60 seconds contained, kept so the UI can say
    #: "12.4 req/s (744 in the last minute)" instead of an unexplained decimal.
    current_window_requests: int = 0

    @property
    def success(self) -> int:
        return max(0, self.requests - self.errors)

    @property
    def success_rate(self) -> float:
        return (self.success / self.requests * 100.0) if self.requests else 0.0

    @property
    def error_rate(self) -> float:
        return (self.errors / self.requests * 100.0) if self.requests else 0.0


@dataclass
class MetricsWindow:
    """A complete chart payload: metadata, totals and the filled bucket list."""

    start: datetime
    end: datetime
    bucket_seconds: int
    metric: ApiMetric
    overview: MetricsOverview
    buckets: list[BucketAggregate] = field(default_factory=list)
    #: When the aggregate was computed. Rendered as "Updated 10:42:08" and sent
    #: on every live frame so the client can prove the data is fresh.
    generated_at: datetime | None = None

    def series(self) -> list[float | None]:
        """The selected metric's value for each bucket, in bucket order.

        ``None`` is used for a latency percentile with no samples (an empty
        bucket) rather than 0: the chart must break the line there instead of
        claiming the latency fell to zero milliseconds.
        """
        getter = _SERIES_GETTERS[self.metric]
        return [getter(bucket) for bucket in self.buckets]


def _rate(b: BucketAggregate) -> float | None:
    return b.rate


def _count(b: BucketAggregate) -> float | None:
    return float(b.count)


def _error_rate(b: BucketAggregate) -> float | None:
    return b.error_rate


_SERIES_GETTERS = {
    ApiMetric.RATE: _rate,
    ApiMetric.COUNT: _count,
    ApiMetric.ERROR_RATE: _error_rate,
    ApiMetric.P50: lambda b: b.p50_ms,
    ApiMetric.P95: lambda b: b.p95_ms,
}


def fill_buckets(
    starts: Iterable[datetime],
    measured: dict[datetime, BucketAggregate],
    bucket_seconds: int,
) -> list[BucketAggregate]:
    """Zero-fill the measured buckets across every expected bucket start.

    A bucket with no traffic becomes an explicit ``count=0`` point, so the chart
    draws it at zero. Omitting it would let a chart library connect the two
    neighbouring buckets with a straight line, which reads as sustained traffic
    across a period that was actually idle — a misleading graph about the
    absence of data is worse than a sparse one.
    """
    out: list[BucketAggregate] = []
    for start in starts:
        existing = measured.get(start)
        if existing is not None:
            out.append(existing)
        else:
            out.append(BucketAggregate(start=start, seconds=bucket_seconds, count=0))
    return out
