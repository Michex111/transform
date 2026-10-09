"""Application service for MCP (agent) activity and connection management.

This is the read model for the MCP Activity page. It composes two sources that
were deliberately kept apart: the **authorization** state (grants, from the MCP
OAuth repository — who is allowed to act) and the **activity** (tool
invocations, from the telemetry tables — what they actually did). Neither alone
answers the page's question, and deriving one from the other would put a
behavioral guess in place of a recorded fact: a connection with zero recent
calls is not the same thing as a connection that is not permitted to call.

A note on "connected". This service reports **authorization status** — active,
paused or revoked — and, separately, when the connection was last seen. It does
not claim a live session: a grant that is active but unused for a month is
described as active-and-idle, not "connected", because the only honest evidence
of a live session would be a currently-open transport, which a stateless HTTP
MCP server does not have.
"""

from dataclasses import dataclass, field
from datetime import datetime

from src.application.ports.api_telemetry_port import (
    InvocationTotals,
    McpActivityFilters,
    McpActivityPage,
    TelemetryRepositoryPort,
)
from src.application.ports.mcp_oauth_port import MCPRepositoryPort
from src.application.services.api_telemetry_service import ResolvedRange
from src.domain.security.enitities.agent_grant import AgentGrantStatus
from src.domain.security.value_object.agent_scope import AgentScope


@dataclass
class McpConnectionView:
    """One authorized connection, with its in-window activity folded in."""

    id: str
    client_id: str
    #: Display name supplied by the third-party app at registration. Untrusted
    #: text — rendered as text, never as markup.
    client_name: str
    status: AgentGrantStatus
    scopes: tuple[AgentScope, ...]
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    paused_at: datetime | None = None
    revoked_at: datetime | None = None
    #: Counts within the selected window, from the invocation log.
    requests: int = 0
    errors: int = 0
    denied: int = 0
    #: The most recent tool call ever, which is a stronger "when did this last
    #: do something?" signal than ``last_seen_at`` (a grant is touched on any
    #: authenticated call, including a read that returns nothing).
    last_activity_at: datetime | None = None

    @property
    def is_paused(self) -> bool:
        return self.status is AgentGrantStatus.PAUSED

    @property
    def is_revoked(self) -> bool:
        return self.status is AgentGrantStatus.REVOKED

    @property
    def is_active(self) -> bool:
        return self.status is AgentGrantStatus.ACTIVE


@dataclass
class McpActivitySummary:
    """The three numbers on the MCP Activity page's header."""

    connections: int = 0
    active_connections: int = 0
    paused_connections: int = 0
    revoked_connections: int = 0
    requests: int = 0
    errors: int = 0
    denied: int = 0
    #: Distinct tools invoked in the window, so "which tools are in use?" is
    #: answerable without loading the log.
    tools_used: list[str] = field(default_factory=list)


class McpActivityService:
    """Connection list, activity log and summary for MCP agent access."""

    def __init__(
        self,
        *,
        mcp_repository: MCPRepositoryPort,
        telemetry_repository: TelemetryRepositoryPort,
    ) -> None:
        self._mcp = mcp_repository
        self._telemetry = telemetry_repository

    async def connections(
        self, account_id: int, *, window: ResolvedRange
    ) -> list[McpConnectionView]:
        """Every grant the account owns, with in-window activity attached.

        Two queries regardless of how many connections there are: the grant
        list, then one grouped totals query and one last-activity query.
        """
        grants = await self._mcp.list_grants(account_id)
        grant_ids = [grant.id for grant in grants]
        totals_by_grant = await self._telemetry.invocation_totals_by_grant(
            account_id, start=window.start, end=window.end
        )
        last_by_grant = await self._telemetry.last_invocation_times(account_id, grant_ids)

        views: list[McpConnectionView] = []
        for grant in grants:
            totals = totals_by_grant.get(grant.id, InvocationTotals())
            views.append(
                McpConnectionView(
                    id=grant.id,
                    client_id=grant.client_id,
                    client_name=grant.client_name,
                    status=grant.status,
                    scopes=grant.scopes,
                    created_at=grant.created_at,
                    last_seen_at=grant.last_used_at,
                    paused_at=grant.paused_at,
                    revoked_at=grant.revoked_at,
                    requests=totals.total,
                    errors=totals.errors,
                    denied=totals.denied,
                    last_activity_at=last_by_grant.get(grant.id),
                )
            )
        return views

    async def summary(
        self, account_id: int, *, window: ResolvedRange
    ) -> McpActivitySummary:
        connections = await self.connections(account_id, window=window)
        totals = await self._telemetry.invocation_totals(
            account_id, start=window.start, end=window.end
        )
        tools = await self._telemetry.distinct_tools(
            account_id, start=window.start, end=window.end
        )
        return McpActivitySummary(
            connections=len(connections),
            active_connections=sum(1 for c in connections if c.is_active),
            paused_connections=sum(1 for c in connections if c.is_paused),
            revoked_connections=sum(1 for c in connections if c.is_revoked),
            requests=totals.total,
            errors=totals.errors,
            denied=totals.denied,
            tools_used=tools,
        )

    async def activity(
        self,
        account_id: int,
        *,
        window: ResolvedRange,
        filters: McpActivityFilters | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> McpActivityPage:
        return await self._telemetry.list_invocations(
            account_id,
            start=window.start,
            end=window.end,
            filters=filters,
            cursor=cursor,
            limit=limit,
        )
