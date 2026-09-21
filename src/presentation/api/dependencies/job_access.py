"""Shared HTTP ownership guard for conversion-job endpoints."""

from fastapi import HTTPException, status

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.infrastructure.logging.audit import log_permission_denied


def assert_job_owner(job: ConversionJob | None, user_id: int | None) -> None:
    """Raise 404 unless ``job`` belongs to the authenticated ``user_id``.

    A missing job, an ownerless (guest) job, and a job owned by someone else
    are all reported identically so job IDs are not enumerable. The denial is
    recorded as a ``permission_denied`` audit event, which is the documented
    signal for a possible IDOR / horizontal-privilege attempt
    (docs/security/access-control-policy.md, incident-response-plan.md).
    """
    if not is_job_owner(job, user_id):
        log_permission_denied(
            resource="conversion_job",
            job_id=getattr(job, "job_id", None),
            actor=str(user_id) if user_id is not None else "anonymous",
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
