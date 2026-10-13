"""OAuth scopes for agent (MCP) access to a user's documents.

WHY a dedicated value object: the scopes an agent may hold are a *security*
decision, not a transport detail. Concentrating the vocabulary here means the
MCP tools (which require a scope), the consent screen (which offers them), the
repository (which persists them) and the token endpoint (which honours them)
cannot drift apart — and there is exactly one place that decides that an
unknown scope string is refused rather than granted.

Design rule: **fail closed**. Anything that is not one of the four known scopes
is dropped, never widened. A client that asks for ``documents.admin`` gets
``documents.read``-level treatment (i.e. nothing), not an accidental grant.
"""

from collections.abc import Iterable, Sequence
from enum import StrEnum


class AgentScope(StrEnum):
    """The permissions an AI agent may be granted over a user's documents.

    Values are the OAuth wire strings. They are deliberately coarse: an agent
    that only needs to read and convert must not be handed deletion rights, so
    each capability the product exposes to a model has its own scope.
    """

    DOCUMENTS_READ = "documents.read"
    DOCUMENTS_CONVERT = "documents.convert"
    DOCUMENTS_WRITE = "documents.write"
    DOCUMENTS_DELETE = "documents.delete"
    #: See a user's cloud workspaces: their names, runtime state, files and
    #: execution history. Read-only.
    WORKSPACES_READ = "workspaces.read"
    #: Run code in a user's cloud workspace, and write files into it. Separate
    #: from ``documents.*`` because a workspace is not a document: this scope
    #: grants an agent CPU on the platform, so it is opt-in on its own and is
    #: never implied by any document permission.
    WORKSPACES_EXECUTE = "workspaces.execute"


#: Every scope the authorization server understands, in a stable display order.
ALL_SCOPES: tuple[AgentScope, ...] = (
    AgentScope.DOCUMENTS_READ,
    AgentScope.DOCUMENTS_CONVERT,
    AgentScope.DOCUMENTS_WRITE,
    AgentScope.DOCUMENTS_DELETE,
    AgentScope.WORKSPACES_READ,
    AgentScope.WORKSPACES_EXECUTE,
)

#: ``offline_access`` — a request MARKER, deliberately not an
#: :class:`AgentScope`.
#:
#: It is an OIDC convention rather than a scope this product models: a client
#: sends it to say "issue me a refresh token". Transform always issues one, so the
#: marker grants nothing that is not already granted — and it must never be
#: stored as a capability. A grant row listing it would claim the agent holds a
#: permission the product does not have, and a `covers()` check against it would
#: be a check against nothing.
#:
#: WHY it is advertised at all: several clients (ChatGPT among them) decide
#: whether to attempt a refresh by looking for this scope in
#: ``scopes_supported``. Absent, they conclude refresh is unavailable and drop the
#: connection when the 60-minute access token expires — even though the refresh
#: token was issued and works.
#:
#: The rule that keeps it safe is one line long: it is accepted in a *request* and
#: produced by :func:`normalize_scopes`, which only ever yields capabilities, so it
#: cannot reach a grant, a token record or a consent checkbox.
OFFLINE_ACCESS = "offline_access"

#: Every scope string recognised in a REQUEST: the capabilities plus the markers.
#:
#: This — not :data:`ALL_SCOPES` — is what the server advertises and what its
#: validators accept, so a client asking for a marker is not told its request is
#: unsupported. Anything outside this set is still refused (fail closed).
KNOWN_REQUEST_SCOPES: tuple[str, ...] = (
    *(scope.value for scope in ALL_SCOPES),
    OFFLINE_ACCESS,
)

#: The capability values, for deciding whether a string could ever be granted.
_CAPABILITY_VALUES: frozenset[str] = frozenset(scope.value for scope in ALL_SCOPES)


def is_capability(value: str) -> bool:
    """Whether ``value`` is a grantable capability rather than a request marker.

    Used to keep the two apart at the boundaries: a marker is accepted on input
    and dropped by :func:`normalize_scopes`, so it can never be persisted as
    something the agent holds.
    """
    return value in _CAPABILITY_VALUES

#: Scopes offered by default on the consent screen. ``documents.delete`` is
#: deliberately absent: an agent that can read, convert and save does not need
#: the ability to destroy data, and making destruction opt-in is the whole
#: point of splitting it out.
DEFAULT_SCOPES: tuple[AgentScope, ...] = (
    AgentScope.DOCUMENTS_READ,
    AgentScope.DOCUMENTS_CONVERT,
    AgentScope.DOCUMENTS_WRITE,
)

#: Irreversible capabilities. Used by the consent screen (never pre-checked)
#: and by the toolbox's audit trail (recorded as destructive).
DESTRUCTIVE_SCOPES: frozenset[AgentScope] = frozenset({AgentScope.DOCUMENTS_DELETE})

#: Human wording for each scope, shown on the consent screen. Kept next to the
#: scope list so a new scope cannot be introduced without a user-facing sentence.
SCOPE_DESCRIPTIONS: dict[AgentScope, str] = {
    AgentScope.DOCUMENTS_READ: "See your files and conversions",
    AgentScope.DOCUMENTS_CONVERT: "Convert your files to another format",
    AgentScope.DOCUMENTS_WRITE: "Save new files and organise your Drive",
    AgentScope.DOCUMENTS_DELETE: "Permanently delete your files",
    AgentScope.WORKSPACES_READ: (
        "See your cloud workspaces, their files and their execution history"
    ),
    AgentScope.WORKSPACES_EXECUTE: (
        "Write files in your workspaces and run code there"
    ),
}


def normalize_scopes(values: Iterable[str] | None) -> tuple[AgentScope, ...]:
    """Reduce raw scope strings to the known scopes, in canonical order.

    Unknown values are **dropped** (fail closed), duplicates collapse, and the
    result is ordered by :data:`ALL_SCOPES` so the persisted value and every
    rendered sentence are deterministic. ``None`` and ``""`` yield no scopes.

    This is also what keeps request markers out of storage: it only ever yields
    members of :data:`ALL_SCOPES`, so ``offline_access`` cannot reach a grant or a
    token record through it.
    """
    requested = {value.strip() for value in (values or ()) if value and value.strip()}
    return tuple(scope for scope in ALL_SCOPES if scope.value in requested)


def scope_string(scopes: Sequence[AgentScope]) -> str:
    """Space-delimited ``scope`` parameter value (RFC 6749 wire form)."""
    return " ".join(scope.value for scope in scopes)


def covers(granted: Iterable[AgentScope], required: AgentScope) -> bool:
    """Whether ``granted`` includes ``required``."""
    return required in set(granted)


def is_subset(requested: Iterable[AgentScope], allowed: Iterable[AgentScope]) -> bool:
    """Whether every requested scope is one the caller is allowed to hand out."""
    return set(requested).issubset(set(allowed))
