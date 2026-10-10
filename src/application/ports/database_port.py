from datetime import datetime
from typing import Protocol, Optional
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.security.enitities.api_key import APIKey

class ConversionJobWriteRepositoryPort(Protocol):
    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """Save a conversion job to the database."""
        ...

    async def update_conversion_job(self, job: ConversionJob) -> None:
        """Persist progress updates for an existing job."""
        ...


class ConversionJobRepositoryPort(ConversionJobWriteRepositoryPort, Protocol):
    """Repository interface for conversion jobs, combining read and write operations."""

    async def get_conversion_job(self, job_id: str) -> Optional[ConversionJob]:
        """Retrieve a conversion job from the database by its ID."""
        ...

    async def list_user_history(
        self,
        user_id: int,
        offset: int,
        limit: int,
        since: datetime | None = None,
        origin: JobOrigin | None = None,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's job history (newest first) plus the total count.

        When ``since`` is provided, only jobs created on/after that timestamp
        are returned (used for the time-range filter on the History page).

        When ``origin`` is provided, only jobs recorded with that origin are
        returned. ``None`` (the default) means unfiltered, preserving the
        historical behaviour for every existing caller; the MCP history tool
        passes ``JobOrigin.MCP`` to answer "what did this agent convert?".
        """
        ...

    async def search_jobs(
        self,
        user_id: int,
        *,
        query: str | None = None,
        fmt: str | None = None,
        limit: int = 10,
    ) -> list[ConversionJob]:
        """Return the user's jobs matching a name substring and/or a format.

        ``query`` matches ``input_file`` or ``output_file``
        (case-insensitive substring); ``fmt`` matches ``source_format`` or
        ``target_format``. Newest first, unpaginated (bounded by ``limit``).
        """
        ...

    async def delete_job(self, job_id: str, user_id: int) -> bool:
        """Delete a single job owned by ``user_id``. Returns True when a row was
        removed; False when the job is missing or not owned by the caller."""
        ...

    async def list_by_batch(self, batch_id: str, user_id: int) -> list[ConversionJob]:
        """Every job a batch created, oldest first, scoped to ``user_id``.

        The scope is part of the contract rather than a caller's responsibility:
        a batch id belonging to another account must return nothing.
        """
        ...

    async def list_batch_ids_for_workflow(
        self, workflow_id: str, user_id: int, *, limit: int = 20
    ) -> list[str]:
        """Distinct batch ids produced by one workflow, newest run first."""
        ...

    async def count_batches_for_workflows(
        self, workflow_ids: list[str], user_id: int
    ) -> dict[str, int]:
        """Run counts for several workflows at once, keyed by workflow id."""
        ...

    async def list_user_active_jobs(
        self,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's in-flight jobs (pending/processing/awaiting upload)."""
        ...

    async def count_deletable_history(
        self, user_id: int, since: datetime | None
    ) -> tuple[int, int]:
        """Return ``(deletable, active)`` for a bulk history delete of the window.

        ``deletable`` counts terminal jobs inside the window (what a delete
        would remove); ``active`` counts in-flight jobs inside it (what would be
        kept). Read-only, and evaluated with the same predicate the delete uses.
        """
        ...

    async def delete_history_range(
        self, user_id: int, since: datetime | None
    ) -> tuple[int, int]:
        """Delete the terminal jobs in the window; return ``(deleted, skipped)``.

        In-flight jobs (PENDING/PROCESSING/AWAITING_UPLOAD) are never removed.
        """
        ...

class APIKeyRepositoryPort(Protocol):
    """Repository interface for API keys."""
    async def save(self, api_key: APIKey) -> None:
        """Save an API key to the database."""
        ...

    async def get_by_id(self, api_key_id: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its ID."""
        ...

    async def find_by_key(self, key_hash: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its stored hash."""
        ...

    async def find_by_user(self, user_id: int) -> list[APIKey]:
        """Retrieve all API keys associated with a specific user."""
        ...

    async def update(self, api_key: APIKey) -> None:
        """Update an existing API key in the database."""
        ...

    async def touch_last_used(self, api_key_id: str) -> None:
        """Record usage time for an API key."""
        ...

    async def delete(self, api_key_id: str) -> bool:
        """Delete an API key from the database by its ID."""
        ...