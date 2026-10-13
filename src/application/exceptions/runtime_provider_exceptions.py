"""Failures raised by a runtime provider adapter.

Three distinct classes on purpose, because the API must answer three different
ways — and the difference is the one users actually care about:

* a **client** error (a bad script, a bad path) is the user's to fix;
* a **provider unavailable** error is infrastructure, and the honest answer is
  "we could not reach the execution host", not "your code is wrong";
* a **provider execution** error means the request was fine and the provider
  failed to carry it out.

Collapsing these into one exception is how "your Python script is broken" ends up
being reported for a daemon that is not running.
"""


class RuntimeProviderError(Exception):
    """Base class for every failure raised by a runtime provider adapter."""


class RuntimeProviderUnavailableError(RuntimeProviderError):
    """The provider itself could not be reached (no daemon, network error).

    Carries a user-safe message: the operator's hostname and socket path are
    diagnostics, not something to echo back through a public API.
    """


class RuntimeNotFoundError(RuntimeProviderError):
    """The provider has no such runtime.

    Raised when a stored ``provider_ref`` is stale — the container was removed
    out of band, or the host was rebuilt. Callers treat this as "the runtime must
    be recreated", never as "the workspace is gone".
    """


class ProviderExecutionError(RuntimeProviderError):
    """The provider accepted the request but failed to run it.

    Deliberately *not* used for a program that exits non-zero: that is a
    successful execution with an exit code, and it belongs in the execution
    record rather than in an exception.
    """


class ProviderResourceError(RuntimeProviderError):
    """A durable-resource operation failed (volume create/remove, snapshot)."""
