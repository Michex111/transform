"""Docker Engine implementation of :class:`RuntimeProviderPort`.

Why Docker for the MVP, and what it does *not* give you — read this before
extending it:

**Why.** The API runs on Render's free tier, which exposes no Docker socket and
no volumes, so the API process cannot create containers. The only execution
substrate this repository already has is the Docker daemon on the host that runs
the converter workers, so a workspace runtime is a container on that host. That
choice adds no new service and no new infrastructure, which the brief requires.

**Persistence, precisely.** Two different things survive two different events,
and conflating them would make the API lie:

* ``stop`` then ``start`` restarts the *same container*. Its writable layer is
  kept, so packages installed with ``uv`` are still there. This is not a
  snapshot — the layer belongs to the container.
* Removing the container (an explicit delete, a host rebuild, or the API
  recreating a lost runtime) discards that layer. What remains is the workspace
  **volume** (the user's files, ``pyproject.toml`` and ``uv.lock``) and, if a
  snapshot was saved, the committed **image** (the environment).

The volume is mounted at ``/workspace``; the virtual environment and the uv cache
live at :data:`VENV_PATH` and :data:`UV_CACHE_PATH`, deliberately *outside* the
volume, because ``docker commit`` captures the container filesystem and not
volume mounts. Without that split a snapshot would contain nothing worth reusing.

**What is not enforced.** Network egress is *not* restricted: ``uv add`` has to
reach a package index, so the runtime keeps network access by default. CPU,
memory and PID limits are enforced via cgroup flags, capabilities are dropped and
``no-new-privileges`` is set, but egress and per-workspace disk quota are gaps —
both are documented in ``docs/cloud-workspaces-plan.md`` and are part of the
argument for moving to a managed sandbox provider in production.

**Blocking SDK.** docker-py is synchronous, so every call is wrapped in
``asyncio.to_thread`` — the same pattern the object-storage adapter uses for
Minio. Note that cancelling an ``await`` does not kill the underlying thread; the
container-side ``timeout`` in :meth:`execute` is what actually bounds a runaway
program, not the ``asyncio`` timeout around it.
"""

import asyncio
import io
from pathlib import PurePosixPath
import re
import shlex
import tarfile
import time
from typing import Any, Callable, Mapping, TypeVar
import uuid

from src.application.exceptions.runtime_provider_exceptions import (
    ProviderExecutionError,
    ProviderResourceError,
    RuntimeNotFoundError,
    RuntimeProviderError,
    RuntimeProviderUnavailableError,
)
from src.application.ports.runtime_provider_port import (
    UV_CACHE_PATH,
    VENV_PATH,
    WORKSPACE_ROOT,
    ExecutionOutcome,
    ExecutionRequest,
    ProviderFileEntry,
    ProviderRuntimeStatus,
    RuntimeSpec,
)
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.project_operation import parse_project_operation
from src.domain.workspaces.value_object.runtime_state import RuntimeState

from src.infrastructure.logging.loggers import worker_logger

T = TypeVar("T")

#: Docker container states, mapped to the domain's runtime lifecycle. Anything
#: unrecognised is reported as ``STOPPED`` rather than guessed at: claiming a
#: runtime is usable when we cannot tell is the failure that produces "the
#: execution was submitted to nothing".
_DOCKER_STATE_MAP: dict[str, RuntimeState] = {
    "running": RuntimeState.RUNNING,
    "paused": RuntimeState.RUNNING,
    "created": RuntimeState.STARTING,
    "restarting": RuntimeState.STARTING,
    "exited": RuntimeState.STOPPED,
    "dead": RuntimeState.STOPPED,
    "removing": RuntimeState.STOPPED,
}

#: A skeleton project so that ``uv sync``/``uv add``/``uv run`` all operate on a
#: real project rooted at ``/workspace`` from the very first execution. Without
#: it ``uv run`` would treat the directory as project-less, and the environment a
#: user built would not be the one the next command used.
_SKELETON_PYPROJECT = """\
[project]
name = "workspace"
version = "0.0.0"
description = "Transform cloud workspace"
requires-python = ">=3.14"
dependencies = []
"""

#: Captured output is written to ``/tmp`` inside the container, which the adapter
#: mounts as a size-limited tmpfs. A runaway ``print`` loop therefore fills RAM
#: that is discarded with the container, not the user's durable volume.
_CAPTURE_DIR = "/tmp"
_MAX_CAPTURE_TMPFS = "512m"

_NAME_UNSAFE = re.compile(r"[^a-z0-9_.-]+")


def _safe_name(value: str, fallback: str = "ws") -> str:
    """Lowercase, DNS-safe fragment for a container/volume/image name.

    Dot runs are collapsed as well as unsanitised characters being replaced. Both
    Docker volume names and container names are *names*, not paths, so ``..`` is
    not itself traversable — but a generated resource name has no use for one, and
    its presence is precisely the shape that invites a future reader to treat the
    string as a path.
    """
    cleaned = _NAME_UNSAFE.sub("-", str(value).lower()).strip("-.")
    cleaned = re.sub(r"\.+", ".", cleaned).strip("-.")
    return cleaned[:48] or fallback


def _unsupported_storage_opt(exc: BaseException) -> bool:
    """True when the daemon rejected ``storage_opt`` because it cannot honor it.

    Docker reports this as either "storage-opt is not supported" or an
    "unknown"/"invalid" option error depending on the storage driver, so the
    text match is the only portable signal. A false negative is safe: the create
    simply fails with the daemon's own message.
    """
    text = str(exc).lower()
    return "storage-opt" in text or "storage_opt" in text


def _not_found(exc: BaseException) -> bool:
    try:
        import docker
    except Exception:  # pragma: no cover - docker is a declared dependency
        return False
    return isinstance(exc, docker.errors.NotFound)


def workspace_relative_path(relative_path: str) -> str:
    """Resolve a workspace-relative path to an absolute one inside the runtime.

    Rejects absolute paths and any ``..`` segment. This is the boundary that keeps
    a file operation from reaching outside the workspace — the container has the
    whole image filesystem, so an unchecked ``../../etc/passwd`` would be a
    perfectly ordinary file read as far as Docker is concerned.
    """
    raw = (relative_path or "").strip()
    if raw in ("", "."):
        return WORKSPACE_ROOT
    if "\x00" in raw or raw.startswith("~"):
        raise ProviderResourceError("Invalid workspace path.")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute():
        raise ProviderResourceError("Workspace paths must be relative.")
    parts = [part for part in candidate.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ProviderResourceError("Workspace paths may not contain '..'.")
    if not parts:
        return WORKSPACE_ROOT
    return str(PurePosixPath(WORKSPACE_ROOT).joinpath(*parts))


def _tar_bytes(name: str, payload: bytes) -> bytes:
    """An in-memory tar holding one file, which is what ``put_archive`` wants."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class DockerRuntimeProvider:
    """A runtime per workspace, as a Docker container on this host."""

    def __init__(
        self,
        *,
        base_image: str,
        volume_prefix: str = "transform-ws-state-",
        container_prefix: str = "transform-ws-rt-",
        snapshot_repository: str = "transform-ws-env",
        egress: str = "none",
        proxy_network: str | None = None,
        proxy_url: str | None = None,
        stop_grace_seconds: int = 30,
        create_timeout_seconds: int = 120,
        storage_limit_bytes: int = 0,
        client: Any | None = None,
    ) -> None:
        self.base_image = base_image
        self._volume_prefix = volume_prefix
        self._container_prefix = container_prefix
        self._snapshot_repository = snapshot_repository
        #: ``none`` | ``proxy`` | ``unrestricted``. Validated here as well as in
        #: settings, because the adapter can also be constructed directly (tests,
        #: a future caller) and an unknown value must never degrade to
        #: "unrestricted" by accident.
        self._egress = egress.strip().lower()
        if self._egress not in {"none", "proxy", "unrestricted"}:
            raise RuntimeProviderError(
                f"Unknown egress policy {egress!r}."
            )
        self._proxy_network = proxy_network
        self._proxy_url = proxy_url
        self._stop_grace_seconds = stop_grace_seconds
        self._create_timeout_seconds = create_timeout_seconds
        #: Requested per-container storage cap, in bytes. 0 disables the attempt.
        #: Best-effort: the daemon rejects it unless it is on overlay2-over-XFS
        #: with ``pquota``. Whether it took effect is reported in the runtime's
        #: metadata rather than assumed.
        self._storage_limit_bytes = storage_limit_bytes
        #: Records whether the last create's storage limit was accepted, for
        #: ``describe`` and the runtime metadata.
        self.storage_quota_supported: bool | None = None
        # Injected for tests; ``None`` means "connect on first use".
        self._client_instance = client

    @property
    def egress(self) -> str:
        return self._egress

    @property
    def allows_package_install(self) -> bool:
        """Whether ``uv`` project operations can reach a package index.

        Read by the application layer to refuse a project operation it cannot
        satisfy under an isolated policy, instead of letting the call fail deep
        inside the container with a confusing resolver error.
        """
        return self._egress != "none"

    @property
    def name(self) -> str:
        return "docker"

    # -- plumbing ----------------------------------------------------------

    def _client(self) -> Any:
        """The docker client, or a clear infrastructure error.

        Connection is lazy so that constructing this adapter (which the API does
        on every boot via the dependency factory) cannot fail on a host without a
        daemon — the failure belongs to the request that needs a runtime, not to
        the process that merely mentions one.
        """
        if self._client_instance is None:
            try:
                import docker

                self._client_instance = docker.from_env()
            except Exception as exc:  # noqa: BLE001 - any failure here is "no daemon"
                raise RuntimeProviderUnavailableError(
                    "The Docker daemon is not reachable from this process."
                ) from exc
        return self._client_instance

    def _translate(
        self, exc: BaseException, on_error: type[RuntimeProviderError]
    ) -> RuntimeProviderError:
        if isinstance(exc, RuntimeProviderError):
            return exc
        if _not_found(exc):
            return RuntimeNotFoundError(str(exc) or "Runtime not found.")
        message = str(exc) or exc.__class__.__name__
        return on_error(message)

    async def _run(
        self, fn: Callable[[], T], *, on_error: type[RuntimeProviderError]
    ) -> T:
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:  # noqa: BLE001 - mapped to the provider taxonomy
            raise self._translate(exc, on_error) from exc

    # -- naming ------------------------------------------------------------

    def _volume_name(self, workspace_id: str) -> str:
        return f"{self._volume_prefix}{_safe_name(workspace_id)}"

    def _container_name(self, workspace_id: str) -> str:
        return f"{self._container_prefix}{_safe_name(workspace_id)}"

    def _snapshot_ref(self, workspace_id: str, revision_id: str) -> str:
        tag = f"{_safe_name(workspace_id)}-{_safe_name(revision_id, 'rev')}"
        return f"{self._snapshot_repository}:{tag}"

    def _labels(self, workspace_id: str) -> dict[str, str]:
        return {
            "transform.kind": "workspace-runtime",
            "transform.workspace": _safe_name(workspace_id),
        }

    # -- durable workspace state -------------------------------------------

    def _ensure_volume_sync(self, client: Any, workspace_id: str) -> str:
        name = self._volume_name(workspace_id)
        try:
            client.volumes.get(name)
            return name
        except Exception as exc:  # noqa: BLE001
            if not _not_found(exc):
                raise
        try:
            client.volumes.create(name=name, labels=self._labels(workspace_id))
        except Exception as exc:  # noqa: BLE001
            # A concurrent create is a benign race: confirm the volume exists
            # rather than failing the caller that simply wanted it to be there.
            if not _not_found(exc):
                try:
                    client.volumes.get(name)
                    return name
                except Exception:  # noqa: BLE001
                    raise exc from None
        return name

    async def ensure_workspace_state(self, workspace_id: str) -> str:
        def _ensure() -> str:
            return self._ensure_volume_sync(self._client(), workspace_id)

        return await self._run(_ensure, on_error=ProviderResourceError)

    async def delete_workspace_state(self, workspace_id: str) -> None:
        def _delete() -> None:
            client = self._client()
            name = self._volume_name(workspace_id)
            try:
                volume = client.volumes.get(name)
            except Exception as exc:  # noqa: BLE001
                if _not_found(exc):
                    return
                raise
            try:
                volume.remove(force=True)
            except Exception as exc:  # noqa: BLE001
                if not _not_found(exc):
                    raise

        await self._run(_delete, on_error=ProviderResourceError)

    # -- runtime lifecycle --------------------------------------------------

    def _get_container(self, client: Any, provider_ref: str) -> Any:
        try:
            return client.containers.get(provider_ref)
        except Exception as exc:  # noqa: BLE001
            if _not_found(exc):
                raise RuntimeNotFoundError(
                    "The runtime no longer exists on the provider."
                ) from exc
            raise

    def _status(self, container: Any) -> ProviderRuntimeStatus:
        container.reload()
        state = _DOCKER_STATE_MAP.get(str(container.status), RuntimeState.STOPPED)
        image = ""
        try:
            tags = container.image.tags
            image = tags[0] if tags else ""
        except Exception:  # noqa: BLE001 - image may have been removed
            image = ""
        attrs = container.attrs or {}
        metadata: dict[str, Any] = {
            "docker_status": str(container.status),
            "created": attrs.get("Created"),
            "image_id": attrs.get("Image"),
        }
        return ProviderRuntimeStatus(
            provider_ref=container.id,
            state=state,
            image=image,
            metadata=metadata,
        )

    def _put_workspace_file(self, container: Any, absolute_path: str, payload: bytes) -> None:
        """Write ``payload`` to ``absolute_path`` inside the workspace.

        ``put_archive`` extracts a tar at a destination directory and creates any
        intermediate directories named *inside* the tar — but the destination
        directory itself must already exist, or the daemon answers
        ``404 ... Could not find the file``.

        That is why every write goes through here with the workspace ROOT as the
        destination and the target's workspace-relative path as the arcname:
        putting a nested path directly (``/workspace/.transform/executions``,
        ``/workspace/src``) fails on the first write into a directory that does
        not exist yet — which is every script execution and every nested file
        write on a fresh workspace. ``_seed_project_sync`` happens to work
        because it targets the root itself, which is the trap this helper
        removes.
        """
        relative = PurePosixPath(absolute_path).relative_to(WORKSPACE_ROOT)
        container.put_archive(WORKSPACE_ROOT, _tar_bytes(str(relative), payload))

    def _seed_project_sync(self, container: Any) -> None:
        """Write the skeleton ``pyproject.toml`` when the workspace lacks one.

        Only when absent: an existing file is the user's environment, and
        overwriting it would silently discard their dependency declarations on
        every restart.
        """
        probe = container.exec_run(
            ["sh", "-c", f"test -f {WORKSPACE_ROOT}/pyproject.toml"], demux=False
        )
        if probe.exit_code == 0:
            return
        container.put_archive(
            WORKSPACE_ROOT, _tar_bytes("pyproject.toml", _SKELETON_PYPROJECT.encode())
        )
    async def create_runtime(self, spec: RuntimeSpec) -> ProviderRuntimeStatus:
        def _create() -> ProviderRuntimeStatus:
            client = self._client()
            self._ensure_volume_sync(client, spec.workspace_id)
            name = self._container_name(spec.workspace_id)

            # A container left behind by an earlier lifecycle would collide on the
            # name. Removing it is safe: the workspace state is a volume, not the
            # container, and a fresh create is exactly the "runtime was replaced"
            # path the persistence design accounts for.
            try:
                stale = client.containers.get(name)
            except Exception as exc:  # noqa: BLE001
                if not _not_found(exc):
                    raise
            else:
                stale.remove(force=True)

            profile = spec.resource_profile
            environment = {
                "HOME": "/tmp",
                "UV_PROJECT_ENVIRONMENT": VENV_PATH,
                "UV_CACHE_DIR": UV_CACHE_PATH,
                "UV_LINK_MODE": "copy",
                **dict(spec.environment),
            }
            network = self._network_kwargs()
            environment.update(network.pop("environment", {}))
            run_kwargs: dict[str, Any] = {
                "image": spec.image or self.base_image,
                # A long-lived idle process: the container is a host for
                # ``docker exec``, and Python is started per execution so that no
                # two executions share an interpreter.
                "command": ["sleep", "infinity"],
                "detach": True,
                "name": name,
                "hostname": _safe_name(spec.workspace_id),
                "working_dir": WORKSPACE_ROOT,
                "volumes": {
                    self._volume_name(spec.workspace_id): {
                        "bind": WORKSPACE_ROOT,
                        "mode": "rw",
                    }
                },
                "environment": environment,
                "labels": {**self._labels(spec.workspace_id), **dict(spec.labels)},
                "mem_limit": f"{profile.memory_mb}m",
                "nano_cpus": int(profile.cpu_cores * 1_000_000_000),
                "pids_limit": profile.pids_limit,
                "security_opt": ["no-new-privileges"],
                # The capabilities matter to the egress policy, not just to
                # general hardening: with CAP_NET_ADMIN gone and no-new-privileges
                # set, a process inside a ``network_mode: none`` container cannot
                # bring an interface up or rewrite its own rules, so "no network"
                # is a real boundary rather than a configuration hint.
                "cap_drop": ["ALL"],
                "tmpfs": {_CAPTURE_DIR: f"rw,size={_MAX_CAPTURE_TMPFS},mode=1777"},
                "restart_policy": {"Name": "no"},
                "init": True,
                **network,
            }
            if self._storage_limit_bytes > 0:
                # Best effort, and deliberately not fatal: only overlay2-on-XFS
                # with pquota (or devicemapper) accepts this. Recording whether it
                # was applied is what stops us claiming a hard cap we did not get.
                run_kwargs["storage_opt"] = {"size": str(self._storage_limit_bytes)}
            try:
                container = client.containers.run(**run_kwargs)
            except Exception as exc:  # noqa: BLE001
                if self._storage_limit_bytes > 0 and _unsupported_storage_opt(exc):
                    self.storage_quota_supported = False
                    worker_logger.warning(
                        "The Docker daemon does not support a container storage "
                        "limit; the workspace quota is enforced by accounting only."
                    )
                    run_kwargs.pop("storage_opt", None)
                    container = client.containers.run(**run_kwargs)
                else:
                    raise
            else:
                if self._storage_limit_bytes > 0:
                    self.storage_quota_supported = True
            self._seed_project_sync(container)
            status = self._status(container)
            return ProviderRuntimeStatus(
                provider_ref=status.provider_ref,
                state=status.state,
                image=status.image,
                metadata={
                    **dict(status.metadata),
                    "egress": self._egress,
                    "storage_quota_enforced": bool(
                        self._storage_limit_bytes > 0
                        and self.storage_quota_supported is True
                    ),
                },
            )

        return await asyncio.wait_for(
            self._run(_create, on_error=ProviderResourceError),
            timeout=self._create_timeout_seconds,
        )

    def _network_kwargs(self) -> dict[str, Any]:
        """The container kwargs that implement the configured egress policy.

        The three policies are three genuinely different network topologies, not
        three settings for one topology:

        * ``none`` — ``network_mode: none``. The container has a loopback
          interface and nothing else. This is Docker's own isolation, applied
          before the process starts.
        * ``proxy`` — attached only to the operator's **internal** network. An
          internal network has no route off the host, so even a runtime that
          ignores the proxy variables cannot reach the internet; the proxy
          container (which is on both that network and a normal one) is the only
          way out. Requires the network to exist, and says so clearly when it
          does not, because falling back would silently mean "unrestricted".
        * ``unrestricted`` — the default bridge. Recorded as such.
        """
        if self._egress == "none":
            return {"network_mode": "none"}
        if self._egress == "proxy":
            network = self._proxy_network
            if not network:  # pragma: no cover - settings validate this at boot
                raise RuntimeProviderError(
                    "The proxy egress policy requires a proxy network."
                )
            client = self._client()
            try:
                client.networks.get(network)
            except Exception as exc:  # noqa: BLE001
                if _not_found(exc):
                    raise RuntimeProviderError(
                        f"The proxy network {network!r} does not exist on this "
                        "host. Create it as an internal network before starting a "
                        "runtime (see docs/cloud-workspaces.md)."
                    ) from exc
                raise
            url = self._proxy_url or ""
            return {
                "network": network,
                "environment": {
                    "HTTP_PROXY": url,
                    "HTTPS_PROXY": url,
                    "http_proxy": url,
                    "https_proxy": url,
                    # uv honours its own index variable, so the proxy is used for
                    # resolution too rather than only for plain HTTP.
                    "UV_INDEX_URL": url or "http://127.0.0.1:1/simple",
                },
            }
        return {}


    async def start_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        def _start() -> ProviderRuntimeStatus:
            container = self._get_container(self._client(), provider_ref)
            container.start()
            return self._status(container)

        return await asyncio.wait_for(
            self._run(_start, on_error=ProviderResourceError),
            timeout=self._create_timeout_seconds,
        )

    async def stop_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        def _stop() -> ProviderRuntimeStatus:
            container = self._get_container(self._client(), provider_ref)
            container.stop(timeout=self._stop_grace_seconds)
            return self._status(container)

        return await asyncio.wait_for(
            self._run(_stop, on_error=ProviderResourceError),
            # The grace period plus a margin: a stop that overruns this is a hung
            # daemon, and reporting it is better than hanging the request.
            timeout=self._stop_grace_seconds + 60,
        )

    async def inspect_runtime(self, provider_ref: str) -> ProviderRuntimeStatus | None:
        def _inspect() -> ProviderRuntimeStatus | None:
            client = self._client()
            try:
                container = client.containers.get(provider_ref)
            except Exception as exc:  # noqa: BLE001
                if _not_found(exc):
                    return None
                raise
            return self._status(container)

        return await self._run(_inspect, on_error=ProviderResourceError)

    async def delete_runtime(self, provider_ref: str) -> None:
        def _delete() -> None:
            client = self._client()
            try:
                container = client.containers.get(provider_ref)
            except Exception as exc:  # noqa: BLE001
                if _not_found(exc):
                    return
                raise
            # ``v=False``: the workspace volume is named and must outlive the
            # container. Removing the container is not a data deletion.
            container.remove(force=True, v=False)

        await self._run(_delete, on_error=ProviderResourceError)

    # -- execution ----------------------------------------------------------

    def _prepare_script(self, container: Any, request: ExecutionRequest) -> tuple[str, ...]:
        """Materialise a submitted script and return the argv to execute it."""
        relative = f".transform/executions/{uuid.uuid4().hex}.py"
        self._put_workspace_file(
            container, f"{WORKSPACE_ROOT}/{relative}", request.source.encode()
        )
        # ``uv run`` resolves the workspace project, so a script sees exactly the
        # environment ``uv add``/``uv sync`` built — which is the whole promise of
        # a persistent workspace.
        return ("uv", "run", "--no-sync", "python", f"{WORKSPACE_ROOT}/{relative}")

    def _read_capture(self, container: Any, path: str, limit: int) -> bytes:
        quoted = shlex.quote(path)
        result = container.exec_run(
            [
                "/bin/sh",
                "-c",
                f"if [ -f {quoted} ]; then head -c {int(limit)} {quoted}; fi",
            ],
            demux=False,
        )
        output = result.output
        return output if isinstance(output, bytes) else b""

    async def execute(
        self, provider_ref: str, request: ExecutionRequest
    ) -> ExecutionOutcome:
        def _execute() -> ExecutionOutcome:
            client = self._client()
            container = self._get_container(client, provider_ref)
            container.reload()
            if str(container.status) != "running":
                raise ProviderExecutionError(
                    "The runtime is not running; start it before executing."
                )

            workdir = workspace_relative_path(request.workdir)
            if request.mode is ExecutionMode.SCRIPT:
                argv = self._prepare_script(container, request)
            elif request.mode is ExecutionMode.PROJECT:
                # Validated against the closed allow-list by the domain parser;
                # the adapter only turns the operation into argv.
                argv = tuple(parse_project_operation(request.source).uv_argv)
            else:
                # COMMAND mode carries its own shell text in ``source``.
                argv = ()
            script = self._build_script(request, argv, workdir)

            started = time.monotonic()
            container.exec_run(["/bin/sh", "-c", script], demux=False)
            elapsed_ms = int((time.monotonic() - started) * 1000)

            limit = max(1, int(request.max_output_bytes))
            stdout = self._read_capture(container, "/tmp/tf.out", limit)
            stderr = self._read_capture(container, "/tmp/tf.err", limit)
            code_raw = self._read_capture(container, "/tmp/tf.code", 24).decode().strip()
            timeout_raw = self._read_capture(container, "/tmp/tf.timeout", 2).decode().strip()

            timed_out = timeout_raw == "1"
            exit_code: int | None
            try:
                exit_code = int(code_raw)
            except ValueError:
                exit_code = None
            if timed_out:
                # No process result exists: the program was killed, so reporting
                # its would-be exit status would be an invention.
                exit_code = None

            for path in ("/tmp/tf.out", "/tmp/tf.err", "/tmp/tf.code", "/tmp/tf.timeout"):
                container.exec_run(["/bin/sh", "-c", f"rm -f {shlex.quote(path)}"])

            return ExecutionOutcome(
                exit_code=exit_code,
                stdout=stdout.decode("utf-8", "replace"),
                stderr=stderr.decode("utf-8", "replace"),
                duration_ms=elapsed_ms,
                timed_out=timed_out,
            )

        return await asyncio.wait_for(
            self._run(_execute, on_error=ProviderExecutionError),
            # Deliberately outside the container-side timeout: this is a
            # last-resort bound on a wedged daemon, not the execution's limit.
            timeout=min(int(request.timeout_seconds) + 120, 3_600),
        )

    def _build_script(
        self, request: ExecutionRequest, argv: tuple[str, ...], workdir: str
    ) -> str:
        """The container-side shell script that runs one execution.

        The timeout is enforced *inside* the container with ``timeout -s KILL``
        rather than by the caller's ``asyncio`` timeout, because cancelling an
        await around a blocking SDK call does not terminate the process — it just
        stops waiting for it. A program that ignores SIGTERM would otherwise keep
        consuming the runtime's CPU after the API had reported a timeout.

        ``exec`` replaces the shell with the target process in the argv modes, so
        the signal reaches the program itself rather than a wrapper shell.

        The exit code and the timeout flag are written to files rather than taken
        from this command's own status: the wrapper always exits 0, so a non-zero
        program status stays *data* instead of being indistinguishable from the
        wrapper having failed.
        """
        seconds = max(1, int(request.timeout_seconds))
        if request.mode is ExecutionMode.COMMAND:
            invocation = (
                f"timeout -s KILL {seconds} /bin/sh -lc {shlex.quote(request.source)}"
            )
        else:
            quoted = " ".join(shlex.quote(arg) for arg in argv)
            invocation = (
                f"timeout -s KILL {seconds} /bin/sh -c 'exec \"$@\"' tf {quoted}"
            )
        return "\n".join(
            [
                "set -u",
                f"if ! cd {shlex.quote(workdir)} 2>/dev/null; then "
                "printf 'workspace working directory does not exist\\n' > /tmp/tf.err; "
                "printf 127 > /tmp/tf.code; printf 0 > /tmp/tf.timeout; exit 0; fi",
                f"{invocation} > /tmp/tf.out 2> /tmp/tf.err",
                "code=$?",
                'printf "%s" "$code" > /tmp/tf.code',
                'if [ "$code" -eq 137 ]; then printf 1 > /tmp/tf.timeout; '
                "else printf 0 > /tmp/tf.timeout; fi",
                "exit 0",
                "",
            ]
        )

    # -- files --------------------------------------------------------------

    async def write_file(
        self, provider_ref: str, relative_path: str, data: bytes
    ) -> None:
        def _write() -> None:
            container = self._get_container(self._client(), provider_ref)
            self._put_workspace_file(container, workspace_relative_path(relative_path), data)

        await self._run(_write, on_error=ProviderResourceError)

    async def read_file(self, provider_ref: str, relative_path: str) -> bytes:
        def _read() -> bytes:
            container = self._get_container(self._client(), provider_ref)
            absolute = workspace_relative_path(relative_path)
            stream, _stat = container.get_archive(absolute)
            buffer = io.BytesIO()
            for chunk in stream:
                buffer.write(chunk)
            buffer.seek(0)
            with tarfile.open(fileobj=buffer, mode="r") as archive:
                members = archive.getmembers()
                if not members:
                    raise ProviderResourceError("File archive was empty.")
                handle = archive.extractfile(members[0])
                return handle.read() if handle is not None else b""

        return await self._run(_read, on_error=ProviderResourceError)

    async def list_files(
        self, provider_ref: str, relative_path: str = "."
    ) -> list[ProviderFileEntry]:
        """List one directory inside the workspace.

        Requires a running runtime (it is an ``exec``). Reading and writing a
        single file do not — they use the archive API, which works on a stopped
        container — so the API distinguishes the two rather than refusing every
        file operation while a runtime is stopped.
        """
        absolute = workspace_relative_path(relative_path)

        def _list() -> list[ProviderFileEntry]:
            container = self._get_container(self._client(), provider_ref)
            container.reload()
            if str(container.status) != "running":
                raise ProviderExecutionError(
                    "The runtime is not running; start it before browsing files."
                )
            script = (
                "import json,os,sys\n"
                "root=sys.argv[1]\n"
                "out=[]\n"
                "try:\n"
                "    names=sorted(os.listdir(root))\n"
                "except OSError:\n"
                "    names=[]\n"
                "for n in names:\n"
                "    p=os.path.join(root,n)\n"
                "    isdir=os.path.isdir(p)\n"
                "    try:\n"
                "        size=0 if isdir else os.path.getsize(p)\n"
                "    except OSError:\n"
                "        size=0\n"
                "    out.append({'name':n,'is_dir':isdir,'size':size})\n"
                "sys.stdout.write(json.dumps(out))\n"
            )
            result = container.exec_run(
                ["python3", "-c", script, absolute], demux=False
            )
            raw = result.output if isinstance(result.output, bytes) else b""
            import json as _json

            try:
                entries = _json.loads(raw.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 - an unusable listing is an error
                raise ProviderExecutionError(
                    "Could not list the workspace directory."
                ) from None
            # Paths are returned WORKSPACE-RELATIVE, which is what every caller
            # needs and what ``read_file``/``write_file``/``delete_file`` accept.
            # Returning the container's absolute path (or, worse, it with the
            # leading slash stripped, which produced ``workspace/outputs/x``)
            # meant a path from a listing could not be fed back into any other
            # call — and the artifact copy-out silently found nothing, so every
            # artifact was recorded as not downloadable.
            relative_dir = (
                "" if absolute == WORKSPACE_ROOT else absolute[len(WORKSPACE_ROOT) :].strip("/")
            )
            entries_out: list[ProviderFileEntry] = []
            for entry in entries:
                name = str(entry.get("name", ""))
                entries_out.append(
                    ProviderFileEntry(
                        path=f"{relative_dir}/{name}" if relative_dir else name,
                        name=name,
                        is_dir=bool(entry.get("is_dir")),
                        size_bytes=int(entry.get("size") or 0),
                    )
                )
            return entries_out

        return await self._run(_list, on_error=ProviderExecutionError)

    async def delete_file(self, provider_ref: str, relative_path: str) -> None:
        absolute = workspace_relative_path(relative_path)

        def _delete() -> None:
            container = self._get_container(self._client(), provider_ref)
            container.reload()
            if str(container.status) != "running":
                raise ProviderExecutionError(
                    "The runtime is not running; start it before deleting files."
                )
            # ``--`` terminates option parsing, and the path is quoted: a file
            # literally named ``-rf`` must be deletable and must not be read as an
            # option to ``rm``.
            result = container.exec_run(
                ["/bin/sh", "-c", f"rm -rf -- {shlex.quote(absolute)}"], demux=False
            )
            if result.exit_code != 0:
                raise ProviderResourceError(
                    f"Could not delete {relative_path!r} in the workspace."
                )

        await self._run(_delete, on_error=ProviderResourceError)

    # -- disk accounting ----------------------------------------------------

    async def measure_workspace_usage(self, provider_ref: str) -> int:
        """Bytes used by the workspace's durable files, or ``0`` if unmeasurable.

        ``du`` on a running container, which is the only way to get an accurate
        number from a named volume. A stopped container cannot be measured, and
        reporting ``0`` there is deliberate: it is the safe direction for the
        *quota* check (it never blocks a legitimate write), and the caller is
        told separately whether the runtime is running.

        Apparent size (``-b``) and not disk usage (``-s``): the quota is a budget
        the user reasons about in bytes of their own files, and block rounding is
        an implementation detail of the filesystem.
        """

        def _measure() -> int:
            container = self._get_container(self._client(), provider_ref)
            container.reload()
            if str(container.status) != "running":
                return 0
            result = container.exec_run(
                [
                    "/bin/sh",
                    "-c",
                    f"du -sb {shlex.quote(WORKSPACE_ROOT)} 2>/dev/null | "
                    "cut -f1",
                ],
                demux=False,
            )
            raw = result.output if isinstance(result.output, bytes) else b""
            # The first whitespace-separated token is the byte count. Parsed
            # rather than taken whole so the measurement does not depend on the
            # container's ``cut`` behaving as expected.
            token = raw.decode("utf-8", "replace").strip().split(maxsplit=1)
            if not token:
                return 0
            try:
                return max(0, int(token[0]))
            except ValueError:
                return 0

        return await self._run(_measure, on_error=ProviderResourceError)

    # -- snapshots ----------------------------------------------------------

    async def create_snapshot(
        self, provider_ref: str, *, workspace_id: str, revision_id: str
    ) -> str:
        ref = self._snapshot_ref(workspace_id, revision_id)

        def _snapshot() -> str:
            container = self._get_container(self._client(), provider_ref)
            repository, _, tag = ref.partition(":")
            container.commit(
                repository=repository,
                tag=tag or "latest",
                message=f"Transform workspace {workspace_id} revision {revision_id}",
            )
            return ref

        return await self._run(_snapshot, on_error=ProviderResourceError)

    # -- diagnostics --------------------------------------------------------

    async def describe(self) -> Mapping[str, Any]:
        def _describe() -> Mapping[str, Any]:
            client = self._client()
            try:
                version = client.version()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeProviderUnavailableError(str(exc)) from exc
            return {
                "provider": "docker",
                "available": True,
                "engine_version": version.get("Version"),
                "api_version": version.get("ApiVersion"),
                "os": version.get("Os"),
                "arch": version.get("Arch"),
                "base_image": self.base_image,
                "egress": self._egress,
                "allows_package_install": self.allows_package_install,
                "proxy_network": self._proxy_network,
                "storage_quota_requested_bytes": self._storage_limit_bytes,
                "storage_quota_enforced": self.storage_quota_supported,
                "workspace_root": WORKSPACE_ROOT,
                "venv_path": VENV_PATH,
            }

        return await self._run(_describe, on_error=RuntimeProviderUnavailableError)
