"""Resource allocation for a runtime.

A named profile rather than raw numbers in the API: the client picks ``small`` or
``standard``, and the deployment decides what that means. Raw ``cpu``/``memory``
fields in a public request would let a caller ask for the whole host, and would
also freeze a sizing decision into every stored workspace.
"""

from dataclasses import dataclass

from src.domain.workspaces.exceptions import InvalidRuntimeConfigError


@dataclass(frozen=True)
class ResourceProfile:
    """Concrete cgroup-style limits for one runtime.

    ``disk_mb`` is advisory: it is recorded and surfaced, but the Docker adapter
    enforces only CPU, memory and the PID count, because a plain named volume
    has no per-container quota. Saying so here beats pretending a limit exists.
    """

    name: str
    cpu_cores: float
    memory_mb: int
    pids_limit: int
    disk_mb: int


#: The profiles a deployment offers, by name. Deliberately modest defaults: the
#: reference host is a developer machine that also runs the converter workers, so
#: a permissive default would let one workspace starve file conversion.
RESOURCE_PROFILES: dict[str, ResourceProfile] = {
    "small": ResourceProfile(
        name="small", cpu_cores=0.5, memory_mb=512, pids_limit=128, disk_mb=1024
    ),
    "standard": ResourceProfile(
        name="standard", cpu_cores=1.0, memory_mb=2048, pids_limit=256, disk_mb=5120
    ),
    "large": ResourceProfile(
        name="large", cpu_cores=2.0, memory_mb=4096, pids_limit=512, disk_mb=10240
    ),
}

DEFAULT_RESOURCE_PROFILE = "standard"


def resolve_profile(name: str) -> ResourceProfile:
    """Look up a profile by name, or raise a validation error.

    Raising rather than defaulting is the point: a request that asked for
    ``"xlarge"`` must not silently receive ``standard``, because the user would
    then be diagnosing a timeout caused by a size they never chose.
    """
    profile = RESOURCE_PROFILES.get(name)
    if profile is None:
        supported = ", ".join(sorted(RESOURCE_PROFILES))
        raise InvalidRuntimeConfigError(
            f"Unsupported resource profile {name!r}; supported: {supported}."
        )
    return profile
