"""The structured record of one public API request.

WHY a domain event rather than "the ORM row": the fields here — and, more
importantly, the fields that are *absent* — are a privacy contract. There is no
request body, no header map, no query-string value, no raw URL and no
credential anywhere in this type. A capture layer physically cannot persist
something the schema has no place for, which is a stronger guarantee than
"remember not to log it".

Two identifiers matter and are easy to confuse:

* ``account_id`` — the owning Transform account. **Every** dashboard query is
  scoped by it. It is what makes one user's log unreadable to another.
* ``api_key_id`` — which of the account's keys made the call. Nullable, because
  a request authenticated with a session JWT (the SPA itself) has no key. The
  dashboard shows those as "Dashboard session" rather than inventing a name.

Neither is ever taken from a request header the caller controls: both come from
the verified credential, resolved by the same authentication dependency every
other authenticated endpoint uses.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ApiRequestEvent:
    """One captured request, in the shape the log explorer renders."""

    id: str
    #: Owning account. The isolation boundary for every read.
    account_id: int
    #: The API key that authenticated the call, or ``None`` for a session JWT.
    api_key_id: str | None
    #: Correlation id, echoed from ``X-Request-ID`` when the client sent one so
    #: a user can match our row against their own logs, otherwise generated.
    request_id: str
    timestamp: datetime
    method: str
    #: The *matched route template* (``/api/v1/files/{file_id}``), never the raw
    #: path. Two requests to different file ids share one row value, which is
    #: what keeps ids out of the log and makes "requests by endpoint" a finite
    #: grouping instead of one series per file.
    route_template: str
    status_code: int
    duration_ms: float
    #: Deployment the request was served by ("development"/"production"), so a
    #: log read from a staging deployment is never mistaken for production
    #: traffic.
    environment: str | None = None
    #: Byte counts when the framework reported them on the wire. Best-effort by
    #: design: absent ("not measured") is represented as ``None``, never 0, so
    #: the UI does not claim a zero-byte request.
    request_bytes: int | None = None
    response_bytes: int | None = None
    #: The key's display name, resolved by a join at **read** time. Never
    #: written: the capture path only knows the id, and the name must reflect
    #: the key's *current* label rather than whatever it was called at capture
    #: time. Display-safe by construction — a key's ``name`` is user-chosen and
    #: the secret is never selected.
    api_key_name: str | None = None

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


@dataclass(frozen=True)
class McpToolInvocation:
    """One MCP tool call made by an authorized agent.

    Deliberately a separate type from :class:`ApiRequestEvent`: an MCP call is
    not an HTTP request against the public API. It is identified by the *grant*
    (the user's consent for one agent application), it names a *tool* rather
    than a route, and its interesting outcomes include "permission denied" —
    a case with no HTTP status. Merging the two into one table would force every
    reader to remember which half of the columns applied.

    ``client_name`` is denormalised at read time for display. It is untrusted
    text supplied by the third-party application at registration, so it is
    rendered as text and never as markup.
    """

    id: str
    account_id: int
    #: The consent row this call was authorized by. This is the unit the user
    #: can pause or revoke, and the identity that is trustworthy — never the
    #: agent's self-reported name.
    grant_id: str
    client_id: str
    tool_name: str
    #: SUCCESS | ERROR | DENIED — see :class:`ToolOutcome`.
    outcome: str
    created_at: datetime
    #: Coarse failure class (``permission_denied``, ``not_found``, ``invalid``,
    #: ``internal``). Never a raw exception message: those can quote file
    #: contents and internal paths.
    error_category: str | None = None
    duration_ms: float | None = None
    request_id: str | None = None
    #: Display name of the client, resolved at read time. Untrusted text.
    client_name: str | None = None


class ToolOutcome:
    """The outcome vocabulary for an MCP tool call.

    Plain string constants (not an enum) so the value can be written straight to
    a ``VARCHAR`` column without a PostgreSQL type — the same choice the MCP
    OAuth tables make, and for the same ``DuplicateObjectError`` reason.
    """

    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    #: The call was refused by the scope check before it did anything. Kept
    #: distinct from ERROR because it is not a malfunction: it is the access
    #: control working, and a user investigating "what did my agent try?" needs
    #: to see attempts that were blocked.
    DENIED = "DENIED"

    ALL = (SUCCESS, ERROR, DENIED)
