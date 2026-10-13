"""A saved, reusable representation of a workspace's environment.

The point of this record is to make "reuse the environment we already built"
a decision with evidence rather than a guess. It stores:

* what the environment *is* (``config_fingerprint``: base image revision +
  Python + uv + config version + resource profile);
* whether it was actually built (``status``);
* the provider handle that can reproduce it (``provider_snapshot_ref``).

The fingerprint is of the **configuration**, not of the dependency lockfile, and
that is a deliberate limit rather than an oversight: ``uv.lock`` lives inside the
workspace, so reading its digest requires a runtime, and a runtime is exactly
what a start is trying to obtain. Reuse therefore answers "is this snapshot built
from the image and toolchain we are asking for?", and the lockfile is reconciled
*after* the runtime is up by running ``uv sync`` — which is idempotent and is
what makes a snapshot that is merely one dependency behind still correct. The
lockfile digest is recorded separately in ``validation_metadata`` so drift is
visible without being load-bearing. The important case the brief calls out —
reusing a snapshot built on an **obsolete base image** — is caught here, because
the base image revision is part of the fingerprint.

Two rules follow from that and are enforced here rather than in the service:

1. A revision is only usable once ``READY``. A ``BUILDING`` snapshot has no
   provider handle to start from, so offering it as a reuse candidate would turn
   a race into a failed start.
2. A revision is only reusable when it matches **both** the environment
   fingerprint and the base image revision. A snapshot built on an image that has
   since been replaced must not be reused — that is precisely the stale base
   image the brief calls out — and it is cheaper to detect here than to debug a
   container that mysteriously has an old interpreter.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.domain.workspaces.exceptions import InvalidRuntimeTransition
from src.domain.workspaces.value_object.image_revision_status import (
    ImageRevisionStatus,
    can_transition,
    is_usable,
)
from src.domain.workspaces.value_object.runtime_config import DEFAULT_BASE_IMAGE_REVISION

#: A failed snapshot must not be retried by mutating this row. The service
#: creates a NEW revision instead, so the history of what was attempted (and
#: what the last known-good revision was) stays intact.
_MAX_ERROR_LENGTH = 1_000


@dataclass
class RuntimeImageRevision:
    revision_id: str
    workspace_id: str
    provider: str
    config_fingerprint: str
    base_image_revision: str = DEFAULT_BASE_IMAGE_REVISION
    status: ImageRevisionStatus = ImageRevisionStatus.PENDING
    #: Provider handle (a committed image tag/id). ``None`` until ``READY``.
    provider_snapshot_ref: str | None = None
    created_at: datetime | None = None
    ready_at: datetime | None = None
    error: str | None = None
    #: Free-form provider facts worth keeping (image size, digest, …).
    validation_metadata: dict[str, Any] = field(default_factory=dict)

    # -- transitions -------------------------------------------------------

    def _transition(self, target: ImageRevisionStatus) -> None:
        if not can_transition(self.status, target):
            raise InvalidRuntimeTransition(
                f"Cannot move image revision {self.revision_id} "
                f"from {self.status} to {target}."
            )
        self.status = target

    def mark_building(self) -> None:
        self._transition(ImageRevisionStatus.BUILDING)

    def mark_ready(
        self, *, provider_snapshot_ref: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Record a completed snapshot.

        A ``READY`` revision with no provider handle would be unusable, so the
        handle is required rather than optional — the failure mode this prevents
        (a candidate that looks reusable but cannot be started) only shows up at
        the next cold start, which is the worst time to discover it.
        """
        if not provider_snapshot_ref:
            raise InvalidRuntimeTransition(
                "A ready image revision requires a provider snapshot reference."
            )
        self._transition(ImageRevisionStatus.READY)
        self.provider_snapshot_ref = provider_snapshot_ref
        if metadata:
            self.validation_metadata.update(metadata)
        self.ready_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
        """Record a failed snapshot.

        Idempotent, like ``WorkspaceRuntime.mark_failed``: a re-reported failure
        must not raise inside a retry loop.
        """
        message = (error or "Snapshot creation failed.")[:_MAX_ERROR_LENGTH]
        if self.status is ImageRevisionStatus.FAILED:
            self.error = message
            return
        self._transition(ImageRevisionStatus.FAILED)
        self.error = message

    # -- queries -----------------------------------------------------------

    def is_usable(self) -> bool:
        """True when this revision can be used to start a runtime."""
        return is_usable(self.status) and bool(self.provider_snapshot_ref)

    def matches(self, *, config_fingerprint: str, base_image_revision: str) -> bool:
        """Whether this revision may be reused for the given environment.

        Both halves are required. The fingerprint covers the configuration and
        toolchain; the base-image comparison catches the case where the two
        happen to agree while the image underneath them has been replaced
        (a rebuilt image with a patch-level change, say). Either check alone
        leaves a way to start an environment the user did not ask for.
        """
        return (
            self.config_fingerprint == config_fingerprint
            and self.base_image_revision == base_image_revision
        )
