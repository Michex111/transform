"""Dashboard API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class ConversionStats(BaseModel):
    total_jobs: int
    successful_jobs: int
    failed_jobs: int
    total_credits_used: int


class StorageBreakdownEntry(BaseModel):
    """Storage used by one file extension (lowercase, no leading dot)."""

    extension: str
    bytes: int
    file_count: int


class StorageStats(BaseModel):
    used_bytes: int
    limit_bytes: int
    used_percent: float
    file_count: int
    breakdown: list[StorageBreakdownEntry] = Field(default_factory=list)
    # --- additive fields -------------------------------------------------
    # Added so the SPA stops hardcoding limits that the server owns. Optional
    # with defaults so an already-deployed older frontend bundle (which does not
    # know these keys) is unaffected, and so any reader written against the old
    # schema still validates.
    available_bytes: int | None = Field(
        default=None, description="max(0, limit_bytes - used_bytes)"
    )
    max_file_size_bytes: int | None = Field(
        default=None, description="Effective per-file cap for this tier, in bytes"
    )



class DashboardResponse(BaseModel):
    conversion_stats: ConversionStats
    storage_stats: StorageStats
    credit_balance: int
    tier: str
    recent_jobs_count: int
    active_api_keys: int
    # First instant of the next UTC calendar month, or None when the tier has
    # no persistent monthly credits (nothing resets for those users).
    credits_reset_at: datetime | None
