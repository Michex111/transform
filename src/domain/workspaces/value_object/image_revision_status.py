"""The lifecycle of a saved runtime image revision (an environment snapshot).

Making this observable rather than instantaneous is deliberate: ``docker
commit`` on a workspace with a large dependency tree takes real time, can fail,
and must not block the request that triggered it. The record is what lets the UI
say "saving…" honestly and lets a later start know whether it may reuse the
snapshot.
"""

from enum import StrEnum


class ImageRevisionStatus(StrEnum):
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


ALLOWED_IMAGE_REVISION_TRANSITIONS: dict[ImageRevisionStatus, frozenset[ImageRevisionStatus]] = {
    ImageRevisionStatus.PENDING: frozenset(
        {ImageRevisionStatus.BUILDING, ImageRevisionStatus.FAILED}
    ),
    ImageRevisionStatus.BUILDING: frozenset(
        {ImageRevisionStatus.READY, ImageRevisionStatus.FAILED}
    ),
    # Terminal: a failed snapshot keeps the workspace's last known-good
    # reference, so recovery is "save again", which creates a NEW revision
    # rather than mutating this one.
    ImageRevisionStatus.READY: frozenset(),
    ImageRevisionStatus.FAILED: frozenset(),
}


def can_transition(current: ImageRevisionStatus, target: ImageRevisionStatus) -> bool:
    return target in ALLOWED_IMAGE_REVISION_TRANSITIONS.get(current, frozenset())


def is_usable(status: ImageRevisionStatus) -> bool:
    """True when the revision may be used to start a runtime."""
    return status is ImageRevisionStatus.READY
