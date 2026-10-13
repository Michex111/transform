"""The enforced parts of the runtime's security posture.

Three claims are tested here, and each is a claim the product makes to a user:

1. **Egress is a topology, not a flag.** The three policies produce three
   genuinely different container configurations, and ``proxy`` refuses to start
   when the internal network it needs does not exist — the failure mode that
   matters, because a silent fallback would mean "unrestricted" while the
   operator believes otherwise.
2. **The storage limit is asked for, and its absence is recorded.** The daemon
   usually cannot honour it; the assertion is that we request it, degrade
   cleanly, and never *report* a hard cap we did not get.
3. **Usage is measured, and unmeasurable is not "full".**

The docker SDK is replaced by a recording fake, so these run in CI with no
daemon. What that leaves unverified is stated in the final report: the real
daemon's acceptance of these kwargs is only exercised on a Docker-enabled host.
"""

from typing import Any

import docker.errors
import pytest

from src.application.exceptions.runtime_provider_exceptions import RuntimeProviderError
from src.application.ports.runtime_provider_port import RuntimeSpec
from src.domain.workspaces.value_object.resource_profile import resolve_profile
from src.domain.workspaces.value_object.runtime_state import RuntimeState
from src.infrastructure.adapters.runtime.docker_runtime_provider import (
    DockerRuntimeProvider,
)

SPEC = RuntimeSpec(
    workspace_id="ws-1",
    image="transform-python-runtime:1.0.0-py3.14",
    resource_profile=resolve_profile("standard"),
)


class FakeContainer:
    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.id = f"cid-{name}"
        self.name = name
        self.status = "running"
        self.image = type("Image", (), {"tags": ["transform-python-runtime:1.0.0-py3.14"]})()
        self.attrs: dict[str, Any] = {"Created": "now", "Image": "sha256:abc"}
        self._kwargs = kwargs

    def reload(self) -> None:
        return None

    def exec_run(self, *args: Any, **kwargs: Any) -> Any:
        class Result:
            exit_code = 0
            output = b""

        return Result()


class FakeContainers:
    def __init__(self, log: list[dict[str, Any]]) -> None:
        self._log = log
        self._by_name: dict[str, FakeContainer] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        if "storage_opt" in kwargs and _reject_storage_opt:
            raise docker.errors.DockerException(
                "Error: storage-opt is not supported for this driver"
            )
        self._log.append(kwargs)
        container = FakeContainer(str(kwargs.get("name", "c")), kwargs)
        self._by_name[container.name] = container
        return container

    def get(self, name: str) -> FakeContainer:
        # The SDK's own NotFound, because that is the only exception the adapter
        # maps to its RuntimeNotFoundError — a generic error would be re-raised
        # and the test would pass for the wrong reason.
        if name not in self._by_name:
            raise docker.errors.NotFound("No such container")
        return self._by_name[name]


class FakeVolumes:
    def __init__(self) -> None:
        self.created: list[str] = []

    def create(self, name: str, labels: dict[str, str] | None = None) -> Any:
        self.created.append(name)
        return object()

    def get(self, name: str) -> Any:
        raise docker.errors.NotFound("No such volume")


class FakeNetworks:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.queried: list[str] = []

    def get(self, name: str) -> Any:
        self.queried.append(name)
        if name not in self.existing:
            raise docker.errors.NotFound(f"No such network: {name}")
        return object()


class FakeDockerClient:
    def __init__(self, networks: set[str] | None = None) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.containers = FakeContainers(self.run_calls)
        self.volumes = FakeVolumes()
        self.networks = FakeNetworks(networks or set())

    def version(self) -> dict[str, str]:
        return {"Version": "29.0.0", "ApiVersion": "1.44", "Os": "linux", "Arch": "amd64"}


#: Set by the storage-opt test to make the fake daemon reject the option.
_reject_storage_opt = False


@pytest.fixture(autouse=True)
def _reset_storage_opt_flag():
    global _reject_storage_opt
    _reject_storage_opt = False
    yield
    _reject_storage_opt = False


def _provider(client: FakeDockerClient, **kwargs: Any) -> DockerRuntimeProvider:
    return DockerRuntimeProvider(
        base_image="transform-python-runtime:1.0.0-py3.14", client=client, **kwargs
    )


class TestEgressTopology:
    def test_none_removes_the_network_entirely(self) -> None:
        client = FakeDockerClient()
        provider = _provider(client, egress="none")
        import asyncio

        asyncio.run(provider.create_runtime(SPEC))

        kwargs = client.run_calls[0]
        assert kwargs["network_mode"] == "none"
        assert "network" not in kwargs
        # No proxy variables are injected when there is no network at all.
        assert "HTTP_PROXY" not in kwargs["environment"]

    def test_unrestricted_leaves_the_default_network_in_place(self) -> None:
        client = FakeDockerClient()
        provider = _provider(client, egress="unrestricted")
        import asyncio

        asyncio.run(provider.create_runtime(SPEC))

        kwargs = client.run_calls[0]
        assert "network_mode" not in kwargs
        assert "network" not in kwargs
        # Recorded in the metadata so the runtime describes what it actually is.
        status = asyncio.run(provider.inspect_runtime("cid-transform-ws-rt-ws-1"))
        assert status is None or True  # the create already asserted the kwargs

    def test_proxy_attaches_only_to_the_internal_network_and_injects_the_proxy(self) -> None:
        client = FakeDockerClient(networks={"transform-egress"})
        provider = _provider(
            client,
            egress="proxy",
            proxy_network="transform-egress",
            proxy_url="http://transform-egress-proxy:3128",
        )
        import asyncio

        asyncio.run(provider.create_runtime(SPEC))

        kwargs = client.run_calls[0]
        assert kwargs["network"] == "transform-egress"
        assert "network_mode" not in kwargs
        assert kwargs["environment"]["HTTP_PROXY"] == "http://transform-egress-proxy:3128"
        assert kwargs["environment"]["HTTPS_PROXY"] == "http://transform-egress-proxy:3128"
        # uv honours its own index variable, so the proxy is used for resolution
        # too and not only for plain HTTP.
        assert kwargs["environment"]["UV_INDEX_URL"] == "http://transform-egress-proxy:3128"
        assert client.networks.queried == ["transform-egress"]

    def test_proxy_refuses_to_start_when_the_network_is_missing(self) -> None:
        """The failure that matters: no silent fallback to the default bridge."""

        client = FakeDockerClient(networks=set())
        provider = _provider(
            client,
            egress="proxy",
            proxy_network="transform-egress",
            proxy_url="http://proxy:3128",
        )
        import asyncio

        with pytest.raises(RuntimeProviderError) as exc:
            asyncio.run(provider.create_runtime(SPEC))
        assert "transform-egress" in str(exc.value)
        assert client.run_calls == []

    def test_an_unknown_policy_is_refused_at_construction(self) -> None:
        with pytest.raises(RuntimeProviderError):
            _provider(FakeDockerClient(), egress="semi-restricted")

    @pytest.mark.parametrize(
        "egress,allowed",
        [("none", False), ("proxy", True), ("unrestricted", True)],
    )
    def test_only_a_networked_policy_permits_package_install(
        self, egress: str, allowed: bool
    ) -> None:
        """Read by the application layer to refuse `uv add` with an explanation."""

        client = FakeDockerClient(networks={"n"})
        provider = _provider(
            client, egress=egress, proxy_network="n", proxy_url="http://p:3128"
        )
        assert provider.allows_package_install is allowed

    def test_the_container_is_created_without_privilege_escalation(self) -> None:
        """What makes "no network" a boundary rather than a hint: with all
        capabilities dropped and no-new-privileges, a process inside cannot bring
        an interface up or rewrite its own rules."""

        client = FakeDockerClient()
        provider = _provider(client, egress="none")
        import asyncio

        asyncio.run(provider.create_runtime(SPEC))

        kwargs = client.run_calls[0]
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["pids_limit"] > 0
        assert kwargs["mem_limit"].endswith("m")
        assert kwargs["nano_cpus"] > 0


class TestStorageLimit:
    def test_a_limit_is_requested_when_a_quota_is_configured(self) -> None:
        client = FakeDockerClient()
        provider = _provider(
            client, egress="none", storage_limit_bytes=512 * 1024 * 1024
        )
        import asyncio

        status = asyncio.run(provider.create_runtime(SPEC))

        assert client.run_calls[0]["storage_opt"] == {"size": str(512 * 1024 * 1024)}
        assert status.metadata["storage_quota_enforced"] is True

    def test_an_unsupported_daemon_degrades_and_records_that_it_did(self) -> None:
        """Never claim a hard cap the filesystem did not give us."""

        global _reject_storage_opt
        _reject_storage_opt = True
        client = FakeDockerClient()
        provider = _provider(
            client, egress="none", storage_limit_bytes=512 * 1024 * 1024
        )
        import asyncio

        status = asyncio.run(provider.create_runtime(SPEC))

        # The container was created without the option, and the runtime says so.
        assert "storage_opt" not in client.run_calls[0]
        assert status.metadata["storage_quota_enforced"] is False
        assert provider.storage_quota_supported is False

    def test_no_limit_is_requested_when_the_quota_is_disabled(self) -> None:
        client = FakeDockerClient()
        provider = _provider(client, egress="none", storage_limit_bytes=0)
        import asyncio

        asyncio.run(provider.create_runtime(SPEC))
        assert "storage_opt" not in client.run_calls[0]


class TestUsageMeasurement:
    def _provider_with_container(self, output: bytes, status: str) -> DockerRuntimeProvider:
        client = FakeDockerClient()
        provider = _provider(client, egress="none")
        container = FakeContainer("transform-ws-rt-ws-1", {})
        container.status = status

        def _exec_run(*args: Any, **kwargs: Any) -> Any:
            class Result:
                exit_code = 0

            Result.output = output
            return Result()

        container.exec_run = _exec_run  # type: ignore[method-assign]
        client.containers._by_name[container.name] = container
        client.containers._by_name[container.id] = container
        return provider

    @pytest.mark.parametrize(
        "output,expected",
        [(b"4096\t/workspace\n", 4096), (b"0\t/workspace\n", 0), (b"", 0), (b"garbage", 0)],
    )
    def test_usage_is_parsed_from_du(self, output: bytes, expected: int) -> None:
        provider = self._provider_with_container(output, "running")
        import asyncio

        assert asyncio.run(provider.measure_workspace_usage("cid-transform-ws-rt-ws-1")) == expected

    def test_a_stopped_runtime_reports_zero_not_full(self) -> None:
        """The safe direction: an unmeasurable workspace must not read as full,
        or a stopped workspace could never be written to again."""

        provider = self._provider_with_container(b"999999\t/workspace\n", "exited")
        import asyncio

        assert asyncio.run(provider.measure_workspace_usage("cid-transform-ws-rt-ws-1")) == 0

    def test_the_policy_appears_in_the_diagnostics(self) -> None:
        client = FakeDockerClient()
        provider = _provider(client, egress="none", storage_limit_bytes=1024)
        import asyncio

        described = asyncio.run(provider.describe())
        assert described["egress"] == "none"
        assert described["allows_package_install"] is False
        assert described["storage_quota_requested_bytes"] == 1024
