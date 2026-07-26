from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ConversionHistoryItem:
    """Represents one historical conversion job entry."""

    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    output_file: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None


@dataclass(frozen=True)
class ActiveQueueItem:
    """Represents one active queue job entry."""

    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    created_at: datetime


@dataclass(frozen=True)
class Page:
    """Represents pagination parameters."""

    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        """Returns SQL-compatible offset for this page."""
        return (self.page - 1) * self.page_size


@dataclass(frozen=True)
class PaginatedResult[T]:
    """Represents a paginated query result."""

    items: list[T]
    total: int
    page: int
    page_size: int
