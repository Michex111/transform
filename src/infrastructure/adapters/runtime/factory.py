"""Builds the configured runtime provider.

A factory rather than an inline construction in the DI module: the settings →
adapter mapping is the one place that has to change when a managed sandbox
provider is added, and keeping it beside the adapter means the Docker-specific
construction arguments are not spread through the presentation layer.
"""

from src.application.exceptions.runtime_provider_exceptions import (
    RuntimeProviderUnavailableError,
)
from src.infrastructure.adapters.runtime.docker_runtime_provider import (
    DockerRuntimeProvider,
)
from src.infrastructure.config.settings import Settings


def build_runtime_provider(settings: Settings) -> DockerRuntimeProvider:
    """Construct the provider named by ``WORKSPACE_RUNTIME_PROVIDER``.

    Raises rather than falling back. ``Settings.validate_settings`` already
    rejects an unknown value at boot, so reaching the error case here means the
    settings were constructed around validation — and silently substituting
    Docker for a provider an operator asked for would be an isolation surprise,
    not a convenience.
    """
    provider = settings.WORKSPACE_RUNTIME_PROVIDER.strip().lower()
    if provider != "docker":
        raise RuntimeProviderUnavailableError(
            f"No runtime provider adapter is registered for {provider!r}."
        )
    return DockerRuntimeProvider(
        base_image=settings.WORKSPACE_RUNTIME_BASE_IMAGE,
        volume_prefix=settings.WORKSPACE_RUNTIME_VOLUME_PREFIX,
        container_prefix=settings.WORKSPACE_RUNTIME_CONTAINER_PREFIX,
        snapshot_repository=settings.WORKSPACE_RUNTIME_SNAPSHOT_REPOSITORY,
        # The egress policy is a network topology, not a flag: see the adapter.
        egress=settings.WORKSPACE_RUNTIME_EGRESS,
        proxy_network=settings.WORKSPACE_RUNTIME_PROXY_NETWORK,
        proxy_url=settings.WORKSPACE_RUNTIME_PROXY_URL,
        stop_grace_seconds=settings.WORKSPACE_RUNTIME_STOP_GRACE_SECONDS,
        # Best effort at the provider level; advisory-only enforcement lives in
        # the service (see WorkspaceService._check_disk_budget).
        storage_limit_bytes=settings.WORKSPACE_DISK_QUOTA_MB * 1024 * 1024,
    )
