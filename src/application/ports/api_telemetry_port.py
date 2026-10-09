"""Ports for the Developer observability feature.

Two boundaries:

:class:`TelemetryRepositoryPort` — persistence and querying of request events
and MCP tool invocations. Implemented by the SQL repository; faked in tests.

:class:`TelemetryIngestionPort` — the write path used by the middleware and the
MCP tool wrapper. It is deliberately **not** the repository: the whole point of
the ingestion boundary is that the request path never awaits a database, so the
middleware depends on a fire-and-forget sink that can be swapped, bounded and
failed independently of the storage that eventually receives the rows.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
)
from src.domain.telemetry.value_object.request_metrics import BucketAggregate


@dataclass
class ApiLogFilters:
    """The filter set the log explorer and the chart both apply.

    Every field is optional and every one is applied server-side. Notably there
    is no free-text field that could be used to match a *value* the user should
    not see: ``route_query`` matches the route template (a finite, public set),
    and ``request_id`` is an exact match on a correlation id the user was already
    shown. There is no "search the body" affordance because no body is stored.
    """

    api_key_id: str | None = None
    method: str | None = None
    #: Exact status code (``500``) or class (``5xx``) — see ``status_filter``.
    status: str | None = None
    #: Substring match against the route template, case-insensitive.
    route_query: str | None = None
    request_id: str | None = None
    #: ``"success"`` | ``"error"`` | None (both).
    outcome: str | None = None


@dataclass
class EventPage:
    """One page of log rows plus the cursor for the next one."""

    items: list[ApiRequestEvent] = field(default_factory=list)
    #: An opaque cursor for the next page, or ``None`` when this is the last.
    next_cursor: str | None = None


@dataclass
class McpActivityFilters:
    """Filters for the MCP activity log."""

    grant_id: str | None = None
    tool_name: str | None = None
    #: ``SUCCESS`` | ``ERROR`` | ``DENIED``.
    outcome: str | None = None


@dataclass
class McpActivityPage:
    items: list[McpToolInvocation] = field(default_factory=list)
    next_cursor: str | None = None


@dataclass
class InvocationTotals:
    """Counts for the MCP Activity summary cards and per-connection column."""

    total: int = 0
    successes: int = 0
    errors: int = 0
    denied: int = 0


class TelemetryRepositoryPort(Protocol):
    """Read/write access to the observability tables.

    Every read takes ``account_id`` as a required first argument. That is not a
    convention so much as the isolation mechanism: the repository has no method
    that can return another account's rows, so a handler that forgets a check
    cannot accidentally leak one.
    """

    # -- write path (called by the ingestion flusher, never by a request) --

    async def save_events(self, events: Sequence[ApiRequestEvent]) -> None: ...

    async def save_invocations(self, invocations: Sequence[McpToolInvocation]) -> None: ...

    # -- metric aggregation --

    async def aggregate(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        bucket_seconds: int,
        filters: ApiLogFilters | None = None,
    ) -> list[BucketAggregate]: ...

    async def count_in_range(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> int: ...

    # -- log explorer --

    async def list_events(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        filters: ApiLogFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> EventPage: ...

    async def get_event(self, account_id: int, event_id: str) -> ApiRequestEvent | None: ...

    # -- MCP activity --

    async def list_invocations(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        filters: McpActivityFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> McpActivityPage: ...

    async def invocation_totals(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime,
        grant_id: str | None = None,
    ) -> InvocationTotals: ...

    async def invocation_totals_by_grant(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> dict[str, InvocationTotals]:
        """Per-connection counts for the window, in one grouped query.

        A per-connection loop would be N+1 queries for a page that lists every
        connection at once — the shape that is fine with three agents and
        pathological with three hundred.
        """
        ...

    async def last_invocation_times(
        self, account_id: int, grant_ids: Sequence[str]
    ) -> dict[str, datetime]: ...

    async def distinct_tools(
        self, account_id: int, *, start: datetime, end: datetime
    ) -> list[str]:
        """Every tool invoked in the window, for the activity filter.

        A dedicated query rather than deriving the list from a page of activity:
        a page is capped, so a tool used beyond the cap would be missing from the
        filter — and a filter that cannot select something the user can see in
        the log is worse than no filter.
        """
        ...

    # -- retention --

    async def delete_events_before(self, cutoff: datetime) -> int: ...

    async def delete_invocations_before(self, cutoff: datetime) -> int: ...


class TelemetryIngestionPort(Protocol):
    """The non-blocking write path used on the request hot path.

    Implementations must never raise and must never block the caller: telemetry
    failing must not fail the request it was describing. ``stats`` exposes queue
    health so a degraded (dropping) state is observable rather than silent.
    """

    def record_event(self, event: ApiRequestEvent) -> None: ...

    def record_invocation(self, invocation: McpToolInvocation) -> None: ...

    def stats(self) -> dict[str, int]: ...
