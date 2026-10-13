"""Domain tests for the runtime configuration fingerprint and the image revision.

The fingerprint is what decides snapshot reuse, so these tests are the ones that
protect users from being handed an environment they did not ask for.
"""

import pytest

from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.exceptions import (
    InvalidRuntimeConfigError,
    InvalidRuntimeTransition,
)
from src.domain.workspaces.value_object.image_revision_status import ImageRevisionStatus
from src.domain.workspaces.value_object.runtime_config import (
    DEFAULT_UV_VERSION,
    RuntimeConfig,
    environment_fingerprint,
)


class TestRuntimeConfigValidation:
    def test_defaults_describe_the_reference_image(self):
        config = RuntimeConfig()
        assert config.python_version == "3.14"
        assert config.uv_version == DEFAULT_UV_VERSION
        assert config.resource_profile == "standard"

    def test_an_unsupported_python_version_is_refused_clearly(self):
        with pytest.raises(InvalidRuntimeConfigError) as exc:
            RuntimeConfig(python_version="2.7")
        assert "2.7" in str(exc.value)

    def test_blank_identity_fields_are_refused(self):
        # Written out explicitly rather than with a parametrised ``**kwargs``:
        # an unpacked ``dict[str, str]`` makes the call untypeable, and a type
        # error in a test is a type error the gate will hold the build on.
        with pytest.raises(InvalidRuntimeConfigError):
            RuntimeConfig(uv_version="   ")
        with pytest.raises(InvalidRuntimeConfigError):
            RuntimeConfig(base_image="   ")
        with pytest.raises(InvalidRuntimeConfigError):
            RuntimeConfig(base_image_revision="   ")

    def test_an_unknown_resource_profile_is_refused(self):
        with pytest.raises(InvalidRuntimeConfigError):
            RuntimeConfig(resource_profile="enormous")

    def test_the_resource_profile_resolves_to_concrete_limits(self):
        profile = RuntimeConfig(resource_profile="large").profile
        assert profile.cpu_cores > 0
        assert profile.memory_mb > 0


class TestFingerprint:
    def test_the_fingerprint_is_stable_for_equal_configurations(self):
        assert RuntimeConfig().fingerprint == RuntimeConfig().fingerprint

    @pytest.mark.parametrize(
        "change",
        [
            {"python_version": "3.14", "uv_version": "0.11.22"},
            {"base_image_revision": "1.0.1-py3.14"},
            {"resource_profile": "small"},
            {"config_version": 2},
        ],
    )
    def test_any_relevant_change_changes_the_fingerprint(self, change):
        """Every field that decides the environment must take part.

        A field that did not would make two genuinely different environments look
        interchangeable, and the snapshot of one would be handed to the other.
        """
        assert RuntimeConfig().fingerprint != RuntimeConfig(**change).fingerprint

    def test_extra_knobs_take_part_in_the_fingerprint(self):
        base = RuntimeConfig()
        assert base.fingerprint != RuntimeConfig(extra=(("digest", "sha256:abc"),)).fingerprint


class TestEnvironmentFingerprint:
    """The lockfile digest helper: recorded as drift metadata, not load-bearing.

    It is deliberately NOT part of the snapshot match key — ``uv.lock`` lives
    inside the workspace, so its digest cannot be read before a runtime exists.
    """

    def test_a_lockfile_change_changes_the_environment_fingerprint(self):
        config = RuntimeConfig().fingerprint
        assert environment_fingerprint(config, "lock-a") != environment_fingerprint(
            config, "lock-b"
        )

    def test_no_lockfile_is_distinct_from_any_digest(self):
        """A fresh workspace and one with a lockfile are different environments.

        Collapsing them would make drift between "never locked" and "locked"
        invisible in the recorded metadata.
        """
        config = RuntimeConfig().fingerprint
        assert environment_fingerprint(config, None) != environment_fingerprint(config, "x")

    def test_the_same_inputs_are_deterministic(self):
        config = RuntimeConfig().fingerprint
        assert environment_fingerprint(config, "abc") == environment_fingerprint(config, "abc")


class TestRuntimeImageRevision:
    def _revision(self, **overrides) -> RuntimeImageRevision:
        values = {
            "revision_id": "rev-1",
            "workspace_id": "ws-1",
            "provider": "docker",
            "config_fingerprint": "fp-1",
        }
        values.update(overrides)
        return RuntimeImageRevision(**values)

    def test_a_fresh_revision_is_pending_and_not_usable(self):
        revision = self._revision()
        assert revision.status is ImageRevisionStatus.PENDING
        assert revision.is_usable() is False

    def test_a_completed_snapshot_becomes_usable(self):
        revision = self._revision()
        revision.mark_building()
        revision.mark_ready(provider_snapshot_ref="transform-ws-env:ws-1:rev-1")
        assert revision.status is ImageRevisionStatus.READY
        assert revision.is_usable() is True
        assert revision.ready_at is not None

    def test_a_ready_revision_without_a_handle_is_refused(self):
        revision = self._revision()
        revision.mark_building()
        with pytest.raises(InvalidRuntimeTransition):
            revision.mark_ready(provider_snapshot_ref="")

    def test_a_failed_snapshot_keeps_its_reason_and_can_be_reported_twice(self):
        revision = self._revision()
        revision.mark_building()
        revision.mark_failed("commit failed: no space left on device")
        assert revision.status is ImageRevisionStatus.FAILED
        assert "no space left" in (revision.error or "")
        revision.mark_failed("commit failed again")
        assert "again" in (revision.error or "")

    def test_a_failed_snapshot_is_not_usable_and_cannot_be_retried_in_place(self):
        revision = self._revision()
        revision.mark_building()
        revision.mark_failed("boom")
        assert revision.is_usable() is False
        with pytest.raises(InvalidRuntimeTransition):
            revision.mark_building()

    def test_reuse_requires_both_the_fingerprint_and_the_base_image(self):
        revision = self._revision(base_image_revision="1.0.0-py3.14")
        revision.mark_building()
        revision.mark_ready(provider_snapshot_ref="img:1")

        assert revision.matches(
            config_fingerprint="fp-1", base_image_revision="1.0.0-py3.14"
        )
        # A different configuration must not reuse it.
        assert not revision.matches(
            config_fingerprint="fp-2", base_image_revision="1.0.0-py3.14"
        )
        # ...and neither must a snapshot built on a base image that has since
        # been replaced, even when the configuration fingerprint agrees.
        assert not revision.matches(
            config_fingerprint="fp-1", base_image_revision="1.1.0-py3.14"
        )
