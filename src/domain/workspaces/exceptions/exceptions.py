"""Workspace-domain failures.

The hierarchy mirrors ``domain/conversions/exceptions``: one base class per
bounded context so a caller can catch everything this feature raises, plus a
narrow subclass per failure mode so the API can map each to the right status
code without string matching on a message.
"""


class WorkspaceError(Exception):
    """Base class for every failure raised by the workspace domain."""


class InvalidWorkspaceError(WorkspaceError, ValueError):
    """The supplied workspace data is not something this system will store.

    A *validation* failure (an empty name, a name that is too long), so it is
    also a ``ValueError`` — the same shape ``InvalidWorkflowError`` uses.
    """


class InvalidRuntimeConfigError(WorkspaceError, ValueError):
    """The requested runtime configuration is not one this deployment supports.

    Raised before any provider call, so an unsupported Python version is a
    clear 422 rather than a container that fails to start minutes later.
    """


class InvalidRuntimeTransition(WorkspaceError):
    """The runtime is not in a state that permits the requested transition."""


class InvalidExecutionTransition(WorkspaceError):
    """The execution is not in a state that permits the requested transition."""
