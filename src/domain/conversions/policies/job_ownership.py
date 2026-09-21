"""Ownership rule for conversion jobs.

Guest (ownerless) jobs have ``user_id is None``; an authenticated caller must
never be treated as their owner. This predicate is the single place that
encodes that rule so the "``job.user_id is not None and ...``" mistake (which
made the check vacuous for guest jobs) cannot be reintroduced at a call site.
"""

from src.domain.conversions.entities.conversion_job import ConversionJob


def is_job_owner(job: ConversionJob | None, user_id: int | None) -> bool:
    """Return True only when ``job`` belongs to the authenticated ``user_id``.

    ``None`` jobs, ownerless (guest) jobs, and a missing caller identity all
    fail closed.
    """
    if job is None or job.user_id is None or user_id is None:
        return False
    return job.user_id == user_id
