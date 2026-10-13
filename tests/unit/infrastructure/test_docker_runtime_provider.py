"""The Docker adapter's pure parts: path resolution and the execution script.

These are testable without a daemon — and they are the parts most worth testing
here, because they are where a mistake is a *security* mistake rather than an
availability one:

* path resolution is the boundary that keeps a file operation inside the
  workspace; the container has the whole image filesystem, so an unchecked
  ``../../etc/passwd`` is an ordinary file read as far as Docker is concerned;
* the generated shell script decides the execution timeout and where output is
  captured, and a quoting error there would let a client break out of the
  command it asked for.
"""

import pytest

from src.application.exceptions.runtime_provider_exceptions import ProviderResourceError
from src.application.ports.runtime_provider_port import ExecutionRequest, WORKSPACE_ROOT
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.infrastructure.adapters.runtime.docker_runtime_provider import (
    DockerRuntimeProvider,
    workspace_relative_path,
)


@pytest.fixture(name="provider")
def provider_fixture() -> DockerRuntimeProvider:
    # No client is constructed: these methods never touch the daemon.
    return DockerRuntimeProvider(base_image="transform-python-runtime:test")


class TestWorkspacePath:
    def test_the_workspace_root_is_the_default(self):
        assert workspace_relative_path("") == WORKSPACE_ROOT
        assert workspace_relative_path(".") == WORKSPACE_ROOT

    def test_a_relative_path_is_resolved_inside_the_workspace(self):
        assert workspace_relative_path("out/report.csv") == f"{WORKSPACE_ROOT}/out/report.csv"

    def test_redundant_segments_are_collapsed(self):
        assert workspace_relative_path("./a/./b") == f"{WORKSPACE_ROOT}/a/b"

    @pytest.mark.parametrize(
        "bad",
        [
            "/etc/passwd",
            "../outside.txt",
            "a/../../b",
            "..",
            "~/.ssh/id_rsa",
            "a\x00b",
        ],
    )
    def test_a_path_that_could_escape_is_refused(self, bad: str):
        with pytest.raises(ProviderResourceError):
            workspace_relative_path(bad)

    def test_a_traversal_is_refused_even_when_it_would_land_back_inside(self):
        """``a/../b`` is refused rather than normalised.

        Normalising would make the guard depend on a resolver agreeing with the
        shell that later runs the path, which is exactly the kind of assumption
        a symlink defeats.
        """
        with pytest.raises(ProviderResourceError):
            workspace_relative_path("a/../b")


class TestExecutionScript:
    def test_the_timeout_is_enforced_inside_the_container(self, provider):
        script = provider._build_script(
            ExecutionRequest(
                mode=ExecutionMode.SCRIPT, source="print(1)", timeout_seconds=42
            ),
            ("uv", "run", "--no-sync", "python", "/workspace/x.py"),
            WORKSPACE_ROOT,
        )
        # The signal must reach the program itself, not a wrapper shell — and
        # SIGKILL, because a program that ignores SIGTERM would otherwise keep
        # burning CPU after the API had reported a timeout.
        assert "timeout -s KILL 42" in script
        assert "exec \"$@\"" in script

    def test_every_argv_element_is_quoted(self, provider):
        script = provider._build_script(
            ExecutionRequest(
                mode=ExecutionMode.SCRIPT, source="x", timeout_seconds=5
            ),
            ("uv", "run", "python", "/workspace/my file; rm -rf /.py"),
            WORKSPACE_ROOT,
        )
        # The metacharacters are inside single quotes, so the shell cannot act
        # on them.
        assert "'/workspace/my file; rm -rf /.py'" in script

    def test_a_command_is_shell_interpreted_and_quoted_as_one_argument(self, provider):
        script = provider._build_script(
            ExecutionRequest(
                mode=ExecutionMode.COMMAND, source="echo hi && echo there", timeout_seconds=7
            ),
            (),
            WORKSPACE_ROOT,
        )
        assert "/bin/sh -lc 'echo hi && echo there'" in script

    def test_the_exit_code_and_timeout_flag_are_written_to_files(self, provider):
        script = provider._build_script(
            ExecutionRequest(mode=ExecutionMode.SCRIPT, source="x", timeout_seconds=5),
            ("uv", "run", "python", "/workspace/x.py"),
            WORKSPACE_ROOT,
        )
        # Written to files so the wrapper can always exit 0 and a non-zero
        # program status stays *data* rather than looking like a wrapper failure.
        assert "/tmp/tf.code" in script
        assert "/tmp/tf.timeout" in script
        assert "exit 0" in script

    def test_a_missing_working_directory_fails_with_a_clear_code(self, provider):
        script = provider._build_script(
            ExecutionRequest(mode=ExecutionMode.SCRIPT, source="x", timeout_seconds=5),
            ("uv", "run", "python", "/workspace/x.py"),
            f"{WORKSPACE_ROOT}/missing",
        )
        assert "workspace working directory does not exist" in script
        assert "printf 127 > /tmp/tf.code" in script

    def test_output_is_redirected_to_the_tmpfs_capture_files(self, provider):
        script = provider._build_script(
            ExecutionRequest(mode=ExecutionMode.SCRIPT, source="x", timeout_seconds=5),
            ("uv", "run", "python", "/workspace/x.py"),
            WORKSPACE_ROOT,
        )
        # /tmp is a size-limited tmpfs, so a runaway print loop fills RAM that is
        # discarded with the container rather than the user's durable volume.
        assert "> /tmp/tf.out 2> /tmp/tf.err" in script


class TestProviderIdentity:
    def test_the_provider_name_is_stable(self, provider):
        # Persisted on the runtime row, so a rename would orphan existing rows.
        assert provider.name == "docker"

    def test_names_are_derived_from_the_workspace_and_stay_dns_safe(self, provider):
        workspace_id = "WS/../Weird ID"
        volume = provider._volume_name(workspace_id)
        container = provider._container_name(workspace_id)
        for name in (volume, container):
            assert name == name.lower()
            assert all(character.isalnum() or character in "-_." for character in name)
            assert "/" not in name and ".." not in name

    def test_a_traversal_cannot_escape_by_way_of_a_workspace_id(self, provider):
        """A hostile workspace id must not become a path prefix.

        Workspace ids are server-minted today, but the naming layer is the last
        line of defence if that ever changes.
        """
        assert ".." not in provider._volume_name("../../etc")
        assert "/" not in provider._volume_name("../../etc")
