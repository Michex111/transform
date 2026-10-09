"""SQLAlchemy implementation of the telemetry repository.

Three things here are worth reading before changing anything.

**1. Bucketing is done on the integer epoch, not on a database timestamp.**
``floor(epoch / size) * size`` is expressible on both PostgreSQL and SQLite and,
crucially, comes back as a number the repository converts to UTC in Python. The
alternative — rendering a dialect-specific timestamp back to the driver — would
mean SQLite hands back a string and PostgreSQL a ``datetime``, and every caller
would have to care. Numbers do not have that problem.

**2. Percentiles are dialect-adaptive.** PostgreSQL has ``percentile_cont`` and
gets exact, interpolated percentiles in one query. SQLite does not, so on that
dialect the durations are fetched for the window and
:func:`~src.domain.telemetry.value_object.request_metrics.percentile` computes
the same values in Python. This is what lets the aggregation be tested against a
real database in CI while production stays a single efficient query.

**3. Pagination is keyset, not offset.** ``(created_at, id)`` is a stable total
order, and the cursor is that pair. An offset-based page would drift as new
requests arrive (a row could be seen twice or skipped as the window shifts),
which for an append-only log is a correctness bug rather than a cosmetic one.
"""

import base64
import binascii
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Integer,
    case,
    cast,
    delete,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.ports.api_telemetry_port import (
    ApiLogFilters,
    EventPage,
    InvocationTotals,
    McpActivityFilters,
    McpActivityPage,
)
from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
)
from src.domain.telemetry.value_object.request_metrics import (
    FIRST_ERROR_STATUS,
    BucketAggregate,
    percentile,
)
from src.infrastructure.database.models import (
    APIKeyModel,
    ApiRequestEventModel,
    MCPAgentGrantModel,
    McpToolInvocationModel,
)


def _encode_cursor(timestamp: datetime, row_id: str) -> str:
    """Encode a keyset position.

    Base64url of ``"<iso timestamp>|<id>"``, with padding stripped. Opaque to
    the client on purpose: it is a position in an ordering, not a value the UI
    should parse or construct, and making it opaque is what allows the ordering
    to change later without a client change.

    The timestamp is normalised to aware UTC **here**, at the boundary, rather
    than by each caller: a cursor built from a naive driver value (SQLite) would
    be shifted by the server's local offset, and the "older than" comparison
    would then match every row — pagination that returns page one forever, which
    is exactly what the regression test caught.
    """
    raw = f"{_aware(timestamp).astimezone(UTC).isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    """Decode a cursor, returning ``None`` for anything malformed.

    A bad cursor is treated as "no cursor" (i.e. the first page) rather than an
    error: the value comes from the client, and a 400 for a truncated base64
    string would make a transient UI bug look like a server fault.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp_part, separator, row_id = raw.partition("|")
        if not separator or not row_id:
            return None
        parsed = datetime.fromisoformat(timestamp_part)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed, row_id
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def _aware(value: datetime) -> datetime:
    """Normalise a driver-returned datetime to an aware UTC one.

    SQLite hands back **naive** datetimes for a ``DateTime(timezone=True)``
    column. That matters more than it looks: ``_encode_cursor`` calls
    ``astimezone(UTC)``, and on a naive value Python assumes the *local* zone —
    so on any machine west of UTC the cursor would be offset and pagination
    would skip or repeat rows. Normalising at the driver boundary, exactly as
    ``sql_mcp_repo`` does, keeps every comparison downstream honest.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _bucket_epoch_expr(dialect: str, bucket_seconds: int):
    """A SQL expression for the aligned integer-epoch bucket a row belongs to.

    The division is wrapped in an outer ``CAST(... AS INTEGER)`` rather than
    relying on the operands' types: SQLAlchemy renders ``x / y`` as *true*
    division, and SQLite then returns a float — so `CAST(x / y AS INTEGER) * y`
    floors explicitly on every backend instead of silently being a no-op. For
    positive epochs truncation toward zero is exactly ``floor``.
    """
    if dialect == "postgresql":
        return cast(
            func.floor(func.extract("epoch", ApiRequestEventModel.created_at) / bucket_seconds)
            * bucket_seconds,
            BigInteger,
        )
    # SQLite (and anything else without ``extract('epoch', …)``): ``strftime``
    # gives the stored UTC instant as seconds.
    epoch = cast(func.strftime("%s", ApiRequestEventModel.created_at), Integer)
    return cast(epoch / bucket_seconds, Integer) * bucket_seconds


class SQLTelemetryRepository:
    """Persists and queries request events and MCP tool invocations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def _dialect(self) -> str:
        try:
            return self._session.get_bind().dialect.name
        except Exception:  # pragma: no cover - a bind always exists in practice
            return ""

    # ------------------------------------------------------------------
    # Write path (called by the ingestion flusher)
    # ------------------------------------------------------------------

    async def save_events(self, events: Sequence[ApiRequestEvent]) -> None:
        if not events:
            return
        self._session.add_all(
            [
                ApiRequestEventModel(
                    id=event.id,
                    account_id=event.account_id,
                    api_key_id=event.api_key_id,
                    request_id=event.request_id,
                    created_at=event.timestamp,
                    method=event.method,
                    route_template=event.route_template,
                    status_code=event.status_code,
                    duration_ms=event.duration_ms,
                    environment=event.environment,
                    request_bytes=event.request_bytes,
                    response_bytes=event.response_bytes,
                )
                for event in events
            ]
        )
        await self._session.commit()

    async def save_invocations(self, invocations: Sequence[McpToolInvocation]) -> None:
        if not invocations:
            return
        self._session.add_all(
            [
                McpToolInvocationModel(
                    id=item.id,
                    account_id=item.account_id,
                    grant_id=item.grant_id,
                    client_id=item.client_id,
                    tool_name=item.tool_name,
                    outcome=item.outcome,
                    error_category=item.error_category,
                    duration_ms=item.duration_ms,
                    request_id=item.request_id,
                    created_at=item.created_at,
                )
                for item in invocations
            ]
        )
        await self._session.commit()

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _event_conditions(
        self,
        account_id: int,
        start: datetime,
        end: datetime,
        filters: ApiLogFilters | None,
    ) -> list:
        """The WHERE clauses shared by the metric and log queries.

        Factored out so the chart and the table can never disagree about what a
        filter means: both call this, so "errors only" narrows the chart exactly
        as much as it narrows the table.
        """
        conditions = [
            ApiRequestEventModel.account_id == account_id,
            ApiRequestEventModel.created_at >= start,
            ApiRequestEventModel.created_at < end,
        ]
        if filters is None:
            return conditions

        if filters.api_key_id:
            conditions.append(ApiRequestEventModel.api_key_id == filters.api_key_id)

        if filters.method:
            conditions.append(func.upper(ApiRequestEventModel.method) == filters.method.upper())

        if filters.status:
            status = filters.status.strip().lower()
            if len(status) == 3 and status.endswith("xx") and status[0].isdigit():
                base = int(status[0]) * 100
                conditions.append(ApiRequestEventModel.status_code >= base)
                conditions.append(ApiRequestEventModel.status_code < base + 100)
            elif status.isdigit():
                conditions.append(ApiRequestEventModel.status_code == int(status))

        if filters.route_query:
            needle = f"%{filters.route_query.strip()}%"
            conditions.append(ApiRequestEventModel.route_template.ilike(needle))

        if filters.request_id:
            conditions.append(ApiRequestEventModel.request_id == filters.request_id.strip())

        if filters.outcome == "success":
            conditions.append(ApiRequestEventModel.status_code < FIRST_ERROR_STATUS)
        elif filters.outcome == "error":
            conditions.append(ApiRequestEventModel.status_code >= FIRST_ERROR_STATUS)

        return conditions

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    async def aggregate(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        bucket_seconds: int,
        filters: ApiLogFilters | None = None,
    ) -> list[BucketAggregate]:
        dialect = self._dialect()
        bucket = _bucket_epoch_expr(dialect, bucket_seconds)
        conditions = self._event_conditions(account_id, start, end, filters)
        duration = ApiRequestEventModel.duration_ms

        count_col = func.count().label("n")
        error_col = func.sum(
            case((ApiRequestEventModel.status_code >= FIRST_ERROR_STATUS, 1), else_=0)
        ).label("errors")
        avg_col = func.avg(duration).label("avg_ms")

        columns = [bucket.label("bucket"), count_col, error_col, avg_col]

        if dialect == "postgresql":
            columns += [
                func.percentile_cont(0.5).within_group(duration.asc()).label("p50"),
                func.percentile_cont(0.95).within_group(duration.asc()).label("p95"),
                func.percentile_cont(0.99).within_group(duration.asc()).label("p99"),
            ]
            query = select(*columns).where(*conditions).group_by(bucket)
            rows = (await self._session.execute(query)).all()
            return [
                BucketAggregate(
                    start=datetime.fromtimestamp(int(row.bucket), tz=UTC),
                    seconds=bucket_seconds,
                    count=int(row.n or 0),
                    errors=int(row.errors or 0),
                    avg_latency_ms=float(row.avg_ms) if row.avg_ms is not None else None,
                    p50_ms=float(row.p50) if row.p50 is not None else None,
                    p95_ms=float(row.p95) if row.p95 is not None else None,
                    p99_ms=float(row.p99) if row.p99 is not None else None,
                )
                for row in rows
            ]

        # Portable fallback: counts in SQL, percentiles from the durations.
        counts = (await self._session.execute(select(*columns).where(*conditions).group_by(bucket))).all()
        samples: dict[int, list[float]] = {}
        duration_rows = await self._session.execute(
            select(bucket.label("bucket"), duration.label("d")).where(*conditions)
        )
        for row in duration_rows.all():
            if row.d is None:
                continue
            samples.setdefault(int(row.bucket), []).append(float(row.d))

        out: list[BucketAggregate] = []
        for row in counts:
            bucket_key = int(row.bucket)
            values = samples.get(bucket_key, [])
            out.append(
                BucketAggregate(
                    start=datetime.fromtimestamp(bucket_key, tz=UTC),
                    seconds=bucket_seconds,
                    count=int(row.n or 0),
                    errors=int(row.errors or 0),
                    avg_latency_ms=float(row.avg_ms) if row.avg_ms is not None else None,
                    p50_ms=percentile(values, 0.5),
                    p95_ms=percentile(values, 0.95),
                    p99_ms=percentile(values, 0.99),
                )
            )
        return out

    async def count_in_range(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> int:
        result = await self._session.execute(
            select(func.count()).where(
                ApiRequestEventModel.account_id == account_id,
                ApiRequestEventModel.created_at >= start,
                ApiRequestEventModel.created_at < end,
            )
        )
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Log explorer    # ------------------------------------------------------------------

    async def list_events(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        filters: ApiLogFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> EventPage:
        conditions = self._event_conditions(account_id, start, end, filters)
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is not None:
                cursor_time, cursor_id = decoded
                # Keyset: strictly "older than the last row I showed", using the
                # id as the tiebreaker so two rows sharing a timestamp are not
                # both skipped or both repeated.
                conditions.append(
                    or_(
                        ApiRequestEventModel.created_at < cursor_time,
                        (ApiRequestEventModel.created_at == cursor_time)
                        & (ApiRequestEventModel.id < cursor_id),
                    )
                )

        # One extra row to learn whether another page exists, without a COUNT.
        query = (
            select(ApiRequestEventModel, APIKeyModel.name)
            .join(APIKeyModel, APIKeyModel.id == ApiRequestEventModel.api_key_id, isouter=True)
            .where(*conditions)
            .order_by(ApiRequestEventModel.created_at.desc(), ApiRequestEventModel.id.desc())
            .limit(limit + 1)
        )
        rows = (await self._session.execute(query)).all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]

        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1][0]
            next_cursor = _encode_cursor(last.created_at, last.id)

        return EventPage(
            items=[self._to_event(row, key_name) for row, key_name in page_rows],
            next_cursor=next_cursor,
        )

    async def get_event(self, account_id: int, event_id: str) -> ApiRequestEvent | None:
        result = await self._session.execute(
            select(ApiRequestEventModel, APIKeyModel.name)
            .join(APIKeyModel, APIKeyModel.id == ApiRequestEventModel.api_key_id, isouter=True)
            .where(
                ApiRequestEventModel.account_id == account_id,
                ApiRequestEventModel.id == event_id,
            )
        )
        row = result.first()
        return self._to_event(row[0], row[1]) if row is not None else None

    @staticmethod
    def _to_event(row: ApiRequestEventModel, key_name: str | None = None) -> ApiRequestEvent:
        return ApiRequestEvent(
            id=row.id,
            account_id=row.account_id,
            api_key_id=row.api_key_id,
            request_id=row.request_id,
            timestamp=_aware(row.created_at),
            method=row.method,
            route_template=row.route_template,
            status_code=row.status_code,
            duration_ms=row.duration_ms,
            environment=row.environment,
            request_bytes=row.request_bytes,
            response_bytes=row.response_bytes,
            api_key_name=key_name,
        )

    # ------------------------------------------------------------------
    # MCP activity
    # ------------------------------------------------------------------

    async def list_invocations(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        filters: McpActivityFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> McpActivityPage:
        conditions = [
            McpToolInvocationModel.account_id == account_id,
            McpToolInvocationModel.created_at >= start,
            McpToolInvocationModel.created_at < end,
        ]
        if filters is not None:
            if filters.grant_id:
                conditions.append(McpToolInvocationModel.grant_id == filters.grant_id)
            if filters.tool_name:
                conditions.append(McpToolInvocationModel.tool_name == filters.tool_name)
            if filters.outcome:
                conditions.append(McpToolInvocationModel.outcome == filters.outcome)

        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded is not None:
                cursor_time, cursor_id = decoded
                conditions.append(
                    or_(
                        McpToolInvocationModel.created_at < cursor_time,
                        (McpToolInvocationModel.created_at == cursor_time)
                        & (McpToolInvocationModel.id < cursor_id),
                    )
                )

        # LEFT JOIN for the display name: the invocation stores the trustworthy
        # identity (grant/client ids), and the human-readable name is resolved
        # here so a renamed or removed grant cannot leave a nameless row.
        query = (
            select(McpToolInvocationModel, MCPAgentGrantModel.client_name)
            .join(
                MCPAgentGrantModel,
                MCPAgentGrantModel.id == McpToolInvocationModel.grant_id,
                isouter=True,
            )
            .where(*conditions)
            .order_by(McpToolInvocationModel.created_at.desc(), McpToolInvocationModel.id.desc())
            .limit(limit + 1)
        )
        rows = (await self._session.execute(query)).all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]

        next_cursor = None
        if has_more and page_rows:
            last_model = page_rows[-1][0]
            next_cursor = _encode_cursor(last_model.created_at, last_model.id)

        return McpActivityPage(
            items=[self._to_invocation(model, name) for model, name in page_rows],
            next_cursor=next_cursor,
        )

    async def invocation_totals(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        grant_id: str | None = None,
    ) -> InvocationTotals:
        conditions = [
            McpToolInvocationModel.account_id == account_id,
            McpToolInvocationModel.created_at >= start,
            McpToolInvocationModel.created_at < end,
        ]
        if grant_id:
            conditions.append(McpToolInvocationModel.grant_id == grant_id)

        outcome = McpToolInvocationModel.outcome
        result = await self._session.execute(
            select(
                func.count().label("total"),
                func.sum(case((outcome == "SUCCESS", 1), else_=0)).label("successes"),
                func.sum(case((outcome == "ERROR", 1), else_=0)).label("errors"),
                func.sum(case((outcome == "DENIED", 1), else_=0)).label("denied"),
            ).where(*conditions)
        )
        row = result.one()
        return InvocationTotals(
            total=int(row.total or 0),
            successes=int(row.successes or 0),
            errors=int(row.errors or 0),
            denied=int(row.denied or 0),
        )

    async def invocation_totals_by_grant(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> dict[str, InvocationTotals]:
        outcome = McpToolInvocationModel.outcome
        result = await self._session.execute(
            select(
                McpToolInvocationModel.grant_id,
                func.count().label("total"),
                func.sum(case((outcome == "SUCCESS", 1), else_=0)).label("successes"),
                func.sum(case((outcome == "ERROR", 1), else_=0)).label("errors"),
                func.sum(case((outcome == "DENIED", 1), else_=0)).label("denied"),
            )
            .where(
                McpToolInvocationModel.account_id == account_id,
                McpToolInvocationModel.created_at >= start,
                McpToolInvocationModel.created_at < end,
            )
            .group_by(McpToolInvocationModel.grant_id)
        )
        return {
            row.grant_id: InvocationTotals(
                total=int(row.total or 0),
                successes=int(row.successes or 0),
                errors=int(row.errors or 0),
                denied=int(row.denied or 0),
            )
            for row in result.all()
        }

    async def last_invocation_times(
        self, account_id: int, grant_ids: Sequence[str]
    ) -> dict[str, datetime]:
        if not grant_ids:
            return {}
        result = await self._session.execute(
            select(
                McpToolInvocationModel.grant_id,
                func.max(McpToolInvocationModel.created_at).label("last_at"),
            )
            .where(
                McpToolInvocationModel.account_id == account_id,
                McpToolInvocationModel.grant_id.in_(list(grant_ids)),
            )
            .group_by(McpToolInvocationModel.grant_id)
        )
        out: dict[str, datetime] = {}
        for row in result.all():
            if row.last_at is not None:
                out[row.grant_id] = _aware(row.last_at)
        return out

    async def distinct_tools(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> list[str]:
        result = await self._session.execute(
            select(McpToolInvocationModel.tool_name)
            .where(
                McpToolInvocationModel.account_id == account_id,
                McpToolInvocationModel.created_at >= start,
                McpToolInvocationModel.created_at < end,
            )
            .distinct()
            .order_by(McpToolInvocationModel.tool_name)
        )
        return [row for row in result.scalars().all() if row]

    @staticmethod
    def _to_invocation(
        row: McpToolInvocationModel, client_name: str | None
    ) -> McpToolInvocation:
        return McpToolInvocation(
            id=row.id,
            account_id=row.account_id,
            grant_id=row.grant_id,
            client_id=row.client_id,
            tool_name=row.tool_name,
            outcome=row.outcome,
            created_at=_aware(row.created_at),
            error_category=row.error_category,
            duration_ms=row.duration_ms,
            request_id=row.request_id,
            client_name=client_name,
        )

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    async def delete_events_before(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(ApiRequestEventModel).where(ApiRequestEventModel.created_at < cutoff)
        )
        await self._session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_invocations_before(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(McpToolInvocationModel).where(McpToolInvocationModel.created_at < cutoff)
        )
        await self._session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
