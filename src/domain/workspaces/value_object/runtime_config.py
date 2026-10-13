"""The immutable runtime configuration a workspace is built from.

This is the contract between the API, the stored workspace row, the runtime
image and the provider adapter. Everything that decides *what environment a
workspace gets* lives here, which is what makes the environment fingerprint — and
therefore snapshot reuse — well defined.
"""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping

from src.domain.workspaces.exceptions import InvalidRuntimeConfigError
from src.domain.workspaces.value_object.resource_profile import (
    DEFAULT_RESOURCE_PROFILE,
    ResourceProfile,
    resolve_profile,
)

#: The Python version the reference runtime image ships. Versioned explicitly so
#: a workspace is reproducible: an implicit "latest" would mean two workspaces
#: created a week apart could have different interpreters with the same config.
DEFAULT_PYTHON_VERSION = "3.14"

#: The pinned ``uv`` version. Bumped deliberately, together with the base image
#: revision, because the lockfile format and the resolver's behaviour are part of
#: the environment's identity.
DEFAULT_UV_VERSION = "0.11.23"

#: Base-image name and the revision tag that is currently built and published.
DEFAULT_BASE_IMAGE = "transform-python-runtime"
DEFAULT_BASE_IMAGE_REVISION = "1.0.0-py3.14"

#: Bumped when the *shape* or the *meaning* of this configuration changes, so a
#: stored config from an older release is recognised as such rather than being
#: compared field-by-field against a newer one.
RUNTIME_CONFIG_VERSION = 1

#: Interpreters a deployment can actually provide. Kept to what the published
#: base image contains: advertising a version we cannot build would turn a
#: configuration error into a runtime that fails minutes later.
SUPPORTED_PYTHON_VERSIONS: frozenset[str] = frozenset({"3.14"})


def environment_fingerprint(config_fingerprint: str, lockfile_digest: str | None) -> str:
    """Fingerprint of a *concrete* environment, not just its configuration.

    Combines the configuration fingerprint with the ``uv.lock`` digest. Two
    workspaces with identical configuration but different dependency sets must
    not share a snapshot, and a workspace whose lockfile changed must not reuse
    the snapshot taken before the change — this is the value that decides both.

    ``lockfile_digest`` is ``None`` when the workspace has no lockfile yet (a
    fresh workspace, or one whose only work was a script with no dependencies);
    that is a distinct, valid state and fingerprints differently from any digest.
    """
    payload = json.dumps(
        {"config": config_fingerprint, "lockfile": lockfile_digest or ""},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything that decides which environment a workspace gets."""

    python_version: str = DEFAULT_PYTHON_VERSION
    uv_version: str = DEFAULT_UV_VERSION
    base_image: str = DEFAULT_BASE_IMAGE
    base_image_revision: str = DEFAULT_BASE_IMAGE_REVISION
    resource_profile: str = DEFAULT_RESOURCE_PROFILE
    config_version: int = RUNTIME_CONFIG_VERSION
    #: Free-form deployment knobs (e.g. an image digest pin) that must take part
    #: in the fingerprint without becoming first-class domain fields. Frozen via
    #: ``__post_init__`` into a hashable tuple so the dataclass stays hashable.
    extra: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        if self.python_version not in SUPPORTED_PYTHON_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_PYTHON_VERSIONS))
            raise InvalidRuntimeConfigError(
                f"Unsupported python_version {self.python_version!r}; supported: {supported}."
            )
        for field_name, value in (
            ("uv_version", self.uv_version),
            ("base_image", self.base_image),
            ("base_image_revision", self.base_image_revision),
        ):
            if not value or not value.strip():
                raise InvalidRuntimeConfigError(f"{field_name} must not be empty.")
        if self.config_version < 1:
            raise InvalidRuntimeConfigError("config_version must be a positive integer.")
        # Validates the profile name and raises a clear error otherwise.
        resolve_profile(self.resource_profile)

    @property
    def profile(self) -> ResourceProfile:
        """The concrete resource limits this configuration asks for."""
        return resolve_profile(self.resource_profile)

    @property
    def image_ref(self) -> str:
        """The base image reference, e.g. ``transform-python-runtime:1.0.0-py3.14``.

        Name and revision are joined here rather than at the call site so that a
        deployment can never accidentally start a workspace from a floating
        ``latest`` tag: the revision is part of the configuration, and the
        configuration is what is fingerprinted and stored.
        """
        return f"{self.base_image}:{self.base_image_revision}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "uv_version": self.uv_version,
            "base_image": self.base_image,
            "base_image_revision": self.base_image_revision,
            "resource_profile": self.resource_profile,
            "config_version": self.config_version,
            "extra": dict(self.extra),
        }

    @property
    def fingerprint(self) -> str:
        """A stable digest of this configuration.

        Used to decide whether a stored runtime image revision was built from
        *this* configuration. Canonical JSON with sorted keys, so field order in
        the dataclass can never change the value.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "RuntimeConfig":
        """Rebuild a configuration from stored JSON, tolerating missing fields.

        A stored config from before a field existed must not make a workspace
        unreadable: absent keys fall back to the current defaults, and only a
        value that is *present but invalid* raises. That asymmetry is what makes
        a schema addition a non-event instead of a migration of every row.
        """
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise InvalidRuntimeConfigError("Runtime configuration must be an object.")
        extra = raw.get("extra")
        extra_items: tuple[tuple[str, str], ...] = ()
        if isinstance(extra, Mapping):
            extra_items = tuple(sorted((str(k), str(v)) for k, v in extra.items()))
        return cls(
            python_version=str(raw.get("python_version", DEFAULT_PYTHON_VERSION)),
            uv_version=str(raw.get("uv_version", DEFAULT_UV_VERSION)),
            base_image=str(raw.get("base_image", DEFAULT_BASE_IMAGE)),
            base_image_revision=str(
                raw.get("base_image_revision", DEFAULT_BASE_IMAGE_REVISION)
            ),
            resource_profile=str(raw.get("resource_profile", DEFAULT_RESOURCE_PROFILE)),
            config_version=int(raw.get("config_version", RUNTIME_CONFIG_VERSION)),
            extra=extra_items,
        )
