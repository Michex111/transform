"""Application-level failures for the workspace feature.

Distinct from the domain's ``WorkspaceError`` hierarchy on purpose: these describe
*outcomes* a request produces (not found, limit reached, a runtime that is busy),
which the API maps to status codes. The domain errors describe what the model
refuses to do and are mapped separately.
"""


class WorkspaceNotFoundError(Exception):
    """The workspace is missing, or is not owned by the caller.

    One class for both, deliberately: a distinct "exists but is not yours"
    response would be an existence oracle over other people's workspace ids.
    """


class WorkspaceLimitReachedError(Exception):
    """The caller already has the maximum number of workspaces."""


class WorkspaceNotUsableError(Exception):
    """The workspace is archived and being deleted, so it accepts no work."""


class RuntimeNotFoundError(Exception):
    """No runtime has ever been attached to the workspace."""


class RuntimeNotRunningError(Exception):
    """The operation needs a running runtime and there is not one."""


class RuntimeBusyError(Exception):
    """The runtime has work in flight, so this operation would destroy it."""


class SnapshotFailedError(Exception):
    """Creating the environment snapshot failed; the workspace is untouched."""


class WorkspaceQuotaExceededError(Exception):
    """The workspace has reached its disk budget.

    Carries the numbers, because "you are over quota" without the usage and the
    limit leaves the user with no idea how much to delete.
    """

    def __init__(self, message: str, *, used_bytes: int = 0, limit_bytes: int = 0) -> None:
        super().__init__(message)
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes


class PackageInstallNotPermittedError(Exception):
    """The deployment's egress policy cannot reach a package index.

    Raised *before* anything is queued, so the user gets an explanation about the
    configuration instead of a resolver timeout inside a container.
    """
