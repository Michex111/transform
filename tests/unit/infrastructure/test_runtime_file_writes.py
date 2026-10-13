"""Writing files into a runtime: the ``put_archive`` contract.

WHY this file exists. Every write into a workspace goes through Docker's
``put_archive``, and that call has a sharp edge:

* the destination directory **must already exist** on the container, or the
  daemon answers ``404 ... Could not find the file``;
* intermediate directories *named inside the tar* are created by extraction.

Writing a nested path by naming its parent as the destination therefore fails on
the first write — which is **every script execution** and every nested file write
on a fresh workspace. That bug shipped, passed the whole unit suite, and was only
found by running a real container; these tests exist so it cannot come back
without a Docker daemon.

The fake records the destination and the tar contents, so the assertion is on the
actual contract rather than on a helper's internals.
"""

import io
import tarfile
from typing import Any

import pytest

from src.application.ports.runtime_provider_port import WORKSPACE_ROOT
from src.infrastructure.adapters.runtime.docker_runtime_provider import (
    DockerRuntimeProvider,
)


class RecordingContainer:
    """Records ``put_archive`` calls and rejects a missing destination.

    The rejection is the point: a fake that accepted any destination would have
    let the original bug pass. It mirrors the daemon's own behaviour (404 when
    the directory does not exist), and the directory set starts as whatever the
    container genuinely has at creation — only ``/workspace``.
    """

    def __init__(self, existing_dirs: set[str] | None = None) -> None:
        self.existing_dirs = set(existing_dirs or {WORKSPACE_ROOT})
        self.puts: list[tuple[str, list[str]]] = []
        self.execs: list[list[str]] = []

    def put_archive(self, path: str, data: bytes) -> bool:
        if path not in self.existing_dirs:
            raise RuntimeError(
                f"404 Client Error: Could not find the file {path} in container"
            )
        names: list[str] = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as archive:
            for member in archive.getmembers():
                names.append(member.name)
                # Extraction creates the parent directories named in the tar,
                # exactly as the daemon does — which is why a nested arcname
                # works where a nested destination does not.
                parent = member.name.rsplit("/", 1)[0] if "/" in member.name else ""
                parts = parent.split("/") if parent else []
                for index in range(1, len(parts) + 1):
                    self.existing_dirs.add(
                        f"{path}/{'/'.join(parts[:index])}".replace("//", "/")
                    )
        self.puts.append((path, names))
        return True

    def exec_run(self, args: Any, demux: bool = False) -> Any:
        self.execs.append(list(args))

        class Result:
            exit_code = 0
            output = b""

        return Result()

    def reload(self) -> None:
        return None


@pytest.fixture(name="provider")
def provider_fixture() -> DockerRuntimeProvider:
    return DockerRuntimeProvider(base_image="transform-python-runtime:test")


class TestNestedWrites:
    def test_a_nested_write_succeeds_on_a_fresh_workspace(
        self, provider: DockerRuntimeProvider
    ) -> None:
        """The regression: ``src/main.py`` into a container that only has
        ``/workspace``."""

        container = RecordingContainer()
        provider._put_workspace_file(container, f"{WORKSPACE_ROOT}/src/main.py", b"x")

        assert len(container.puts) == 1
        destination, names = container.puts[0]
        # Extracted at the ROOT, with the nested path as the arcname.
        assert destination == WORKSPACE_ROOT
        assert names == ["src/main.py"]

    def test_a_deeply_nested_write_succeeds(
        self, provider: DockerRuntimeProvider
    ) -> None:
        container = RecordingContainer()
        provider._put_workspace_file(
            container, f"{WORKSPACE_ROOT}/a/b/c/d.txt", b"deep"
        )
        assert container.puts[0] == (WORKSPACE_ROOT, ["a/b/c/d.txt"])

    def test_a_root_level_write_still_works(
        self, provider: DockerRuntimeProvider
    ) -> None:
        container = RecordingContainer()
        provider._put_workspace_file(container, f"{WORKSPACE_ROOT}/notes.txt", b"n")
        assert container.puts[0] == (WORKSPACE_ROOT, ["notes.txt"])

    def test_the_naive_nested_destination_would_fail(
        self, provider: DockerRuntimeProvider
    ) -> None:
        """Documents the bug directly, so the reason for the helper is visible.

        If someone "simplifies" this back to naming the parent directory, this
        test explains why that cannot work.
        """
        container = RecordingContainer()
        with pytest.raises(RuntimeError, match="Could not find the file"):
            container.put_archive(f"{WORKSPACE_ROOT}/src", b"")

    def test_the_payload_is_preserved_byte_for_byte(
        self, provider: DockerRuntimeProvider
    ) -> None:
        container = RecordingContainer()
        payload = "print('héllo')\n".encode()
        provider._put_workspace_file(container, f"{WORKSPACE_ROOT}/x.py", payload)

        # Read the tar back and compare: a write that mangled bytes would be a
        # silent corruption of a user's file.
        recorded = _tar_payloads(container, provider, payload)
        assert recorded[f"x.py"] == payload

    def test_a_path_outside_the_workspace_is_refused(
        self, provider: DockerRuntimeProvider
    ) -> None:
        """``relative_to`` is the guard: a path that is not under the workspace
        cannot be expressed as a workspace-relative arcname, so it raises before
        any archive is built."""
        container = RecordingContainer()
        with pytest.raises(ValueError):
            provider._put_workspace_file(container, "/etc/passwd", b"x")
        assert container.puts == []


def _tar_payloads(
    container: RecordingContainer, provider: DockerRuntimeProvider, payload: bytes
) -> dict[str, bytes]:
    """Re-extract the last written archive to compare file contents."""
    # Re-run the write against a capturing container so the exact bytes are read
    # back rather than inferred.
    captured: dict[str, bytes] = {}

    class Capture(RecordingContainer):
        def put_archive(self, path: str, data: bytes) -> bool:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r") as archive:
                for member in archive.getmembers():
                    handle = archive.extractfile(member)
                    if handle is not None:
                        captured[member.name] = handle.read()
            return True

    provider._put_workspace_file(Capture(), f"{WORKSPACE_ROOT}/x.py", payload)
    return captured


class ListingContainer(RecordingContainer):
    """A running container whose ``listdir`` returns a fixed set of names."""

    def __init__(self, names: list[tuple[str, bool, int]]) -> None:
        super().__init__()
        self.status = "running"
        self._names = names

    def exec_run(self, args: Any, demux: bool = False) -> Any:
        self.execs.append(list(args))
        # The listing script prints JSON on stdout.
        import json as _json

        payload = _json.dumps(
            [{"name": n, "is_dir": d, "size": s} for n, d, s in self._names]
        ).encode()

        class Result:
            exit_code = 0
            output = payload

        return Result()


class ListingProvider(DockerRuntimeProvider):
    """Injects a container without a daemon."""

    def __init__(self, container: ListingContainer) -> None:
        super().__init__(base_image="transform-python-runtime:test")
        self._container = container

    def _get_container(self, client: Any, provider_ref: str) -> Any:  # type: ignore[override]
        return self._container

    def _client(self) -> Any:  # type: ignore[override]
        return object()


class TestListingPaths:
    """``list_files`` must return WORKSPACE-RELATIVE paths.

    The port contract says so, and every other method takes a relative path — so a
    listing that returned the container's absolute path could not be fed back into
    ``read_file`` at all. It shipped returning ``workspace/outputs/x`` (the
    absolute path with its leading slash stripped), and the artifact copy-out then
    found nothing: every artifact was recorded as not downloadable.
    """

    def _entries(self, relative: str, names: list[tuple[str, bool, int]]):
        import asyncio

        container = ListingContainer(names)
        provider = ListingProvider(container)
        return asyncio.run(provider.list_files("cid", relative))

    def test_a_root_listing_returns_bare_names(self) -> None:
        entries = self._entries(".", [("main.py", False, 10)])
        assert [e.path for e in entries] == ["main.py"]

    def test_a_subdirectory_listing_is_relative_to_the_workspace(self) -> None:
        entries = self._entries("outputs", [("summary.json", False, 40)])
        assert [e.path for e in entries] == ["outputs/summary.json"]

    def test_a_deep_listing_keeps_the_relative_prefix(self) -> None:
        entries = self._entries("a/b", [("c.txt", False, 1)])
        assert [e.path for e in entries] == ["a/b/c.txt"]

    def test_a_listing_path_can_be_fed_back_into_a_read(self) -> None:
        """The property that was broken: the path round-trips."""
        from src.infrastructure.adapters.runtime.docker_runtime_provider import (
            workspace_relative_path,
        )

        entries = self._entries("outputs", [("summary.json", False, 40)])
        # Relative in, relative out, and it resolves back under the workspace
        # rather than to ``/workspace/workspace/...``.
        assert workspace_relative_path(entries[0].path) == (
            f"{WORKSPACE_ROOT}/outputs/summary.json"
        )

    def test_directories_are_flagged(self) -> None:
        entries = self._entries(".", [("outputs", True, 0)])
        assert entries[0].is_dir is True
        assert entries[0].path == "outputs"

    def test_names_and_sizes_survive(self) -> None:
        entries = self._entries("outputs", [("report.csv", False, 1234)])
        assert entries[0].name == "report.csv"
        assert entries[0].size_bytes == 1234
