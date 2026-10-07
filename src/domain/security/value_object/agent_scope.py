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


#: Every scope the authorization server understands, in a stable display order.
ALL_SCOPES: tuple[AgentScope, ...] = (
    AgentScope.DOCUMENTS_READ,
    AgentScope.DOCUMENTS_CONVERT,
    AgentScope.DOCUMENTS_WRITE,
    AgentScope.DOCUMENTS_DELETE,
)

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
}


def normalize_scopes(values: Iterable[str] | None) -> tuple[AgentScope, ...]:
    """Reduce raw scope strings to the known scopes, in canonical order.

    Unknown values are **dropped** (fail closed), duplicates collapse, and the
    result is ordered by :data:`ALL_SCOPES` so the persisted value and every
    rendered sentence are deterministic. ``None`` and ``""`` yield no scopes.
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
