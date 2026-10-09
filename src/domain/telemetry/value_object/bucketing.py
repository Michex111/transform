"""Time-bucket sizing for the API Logs chart.

WHY this is a domain rule rather than a router concern: the bucket size decides
what the numbers *mean*. A bucket's "rate" is ``count / bucket_seconds`` — the
average over that window — so the server and the chart have to agree on the
size, and a change to it changes the reading of every point. Keeping the rule
here makes it testable without a database and impossible for two callers to
compute differently.

Buckets are aligned to epoch multiples of the chosen size rather than to the
request range's start. That makes a bucket mean the same thing regardless of
when you asked: the 10:00:00 bucket covers 10:00:00–10:00:10 whether the window
begins at 09:57 or at 10:00. It also means two adjacent queries share their
partial edge buckets, so a refresh does not shift the whole series sideways.
"""

from datetime import UTC, datetime

#: Sizes the chart may use, in seconds. Every value divides the next one evenly
#: in practice (they are the familiar 1/5/10/15/30/60 ladder, then minutes), so
#: a finer window is a subdivision of a coarser one and zooming does not
#: re-bucket the data into a shape that no longer lines up.
BUCKET_CHOICES: tuple[int, ...] = (
    1,
    5,
    10,
    15,
    30,
    60,
    300,
    900,
    1800,
    3600,
)

#: Upper bound on the number of buckets a single chart response may contain.
#: A chart with more points than pixels is not more readable, and an unbounded
#: bucket count is an unbounded response payload (and an unbounded client-side
#: array) — a 30-day range at 1-second buckets would be 2.6 million points.
#: The size ladder above is chosen so every supported preset lands well below
#: this with the smallest legal size.
MAX_BUCKETS = 200

#: Target number of points. The ladder picks the smallest size that yields no
#: MORE than this, which keeps short windows detailed and long windows legible
#: instead of forcing one fixed size on every range.
TARGET_BUCKETS = 120

#: The smallest and largest size the server will ever use, used to clamp a
#: caller-supplied override so a request cannot ask for a 1-second bucket over
#: seven days (which the payload bound above would then have to reject).
MIN_BUCKET_SECONDS = BUCKET_CHOICES[0]
MAX_BUCKET_SECONDS = BUCKET_CHOICES[-1]


def bucket_seconds_for_span(span_seconds: float) -> int:
    """Pick a bucket size for a window of ``span_seconds``.

    Returns the smallest size on the ladder that produces no more than
    :data:`TARGET_BUCKETS` points, falling back to the coarsest size for a
    range longer than the ladder can subdivide (the 7-day preset lands on
    hourly buckets, i.e. 168 points).
    """
    if span_seconds <= 0:
        return MIN_BUCKET_SECONDS
    target = span_seconds / TARGET_BUCKETS
    for size in BUCKET_CHOICES:
        if size >= target:
            return size
    return MAX_BUCKET_SECONDS


def clamp_bucket_seconds(bucket_seconds: int) -> int:
    """Clamp an explicit bucket size into the supported ladder range.

    Used for the overview query, which deliberately aggregates the whole window
    into one bucket: that caller passes the span as the size, so the clamp is
    what stops it from being rejected as "too coarse" while still keeping the
    value inside what the SQL bucket expression can express.
    """
    return max(MIN_BUCKET_SECONDS, min(int(bucket_seconds), MAX_BUCKET_SECONDS))


def align_to_bucket(moment: datetime, bucket_seconds: int) -> datetime:
    """Floor ``moment`` to the start of its bucket.

    Buckets are aligned on the epoch, so two windows that overlap share their
    edge buckets exactly. The input must be timezone-aware: a naive datetime
    would be silently interpreted as UTC, which is exactly the class of bug the
    credit-period module already documents, so it is refused instead.
    """
    if moment.tzinfo is None:
        raise ValueError("align_to_bucket requires a timezone-aware datetime")
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % bucket_seconds), tz=UTC)


def bucket_starts(start: datetime, end: datetime, bucket_seconds: int) -> list[datetime]:
    """Every bucket start in ``[align(start), end)``, in ascending order.

    Emitted eagerly so the caller can zero-fill: a gap in the data must render
    as a gap in the chart (or an explicit zero), never as a straight line
    bridging two distant points, which would read as traffic that did not
    happen. The number of points is bounded by :data:`MAX_BUCKETS` on top of
    this — callers must size the window accordingly.
    """
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    if end < start:
        return []
    current = align_to_bucket(start, bucket_seconds)
    out: list[datetime] = []
    while current < end and len(out) < MAX_BUCKETS:
        out.append(current)
        current = datetime.fromtimestamp(current.timestamp() + bucket_seconds, tz=UTC)
    return out
