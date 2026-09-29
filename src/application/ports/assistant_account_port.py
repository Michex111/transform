"""Port for the assistant's read-only view of the caller's account and usage.

WHY this exists: the assistant must be able to answer "how many credits do I
have left?" and "how many PDF conversions did I run yesterday?" with numbers it
did not invent, but it must never be able to *change* credits, storage or jobs
to answer them. A dedicated read-only port (rather than handing the tool the
dashboard's repositories) makes that one-way capability explicit and keeps the
HTTP dashboard free to evolve its own presentation.

The arithmetic deliberately mirrors ``routers/v1/dashboard.py`` — same period
key, same allowance/balance fallback, same storage source — so the assistant can
never contradict the numbers the user sees on the dashboard.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AccountOverview:
    """A point-in-time, read-only snapshot of one user's account and usage.

    ``by_target_format`` is a tuple (not a dict) because it is ordered by count
    and the order is part of what the model should report ("you mostly make
    PDFs"); tuples keep it immutable and JSON-serialisable through the tool
    result.
    """

    tier: str
    credits_remaining: int
    credits_reset_at: datetime | None
    storage_used_bytes: int
    storage_limit_bytes: int
    jobs_total: int
    jobs_completed: int
    jobs_failed: int
    jobs_active: int
    by_target_format: tuple[tuple[str, int], ...]


@runtime_checkable
class AssistantAccountPort(Protocol):
    """Read-only account/usage aggregation for the assistant account tool."""

    async def overview(
        self,
        user_id: int,
        *,
        since: datetime | None,
        fmt: str | None,
        status: str | None,
    ) -> AccountOverview:
        """Summarise ``user_id``'s credits, storage and job counts.

        ``since`` bounds the job statistics to jobs created on/after that
        instant; ``fmt`` matches the source OR target format (case-insensitive);
        ``status`` narrows the job statistics to one job status. All three are
        optional, and ``None`` means "no constraint".
        """
        ...
