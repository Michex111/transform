"""Dashboard API schemas."""

from pydantic import BaseModel


class ConversionStats(BaseModel):
    total_jobs: int
    successful_jobs: int
    failed_jobs: int
    total_credits_used: int


class StorageStats(BaseModel):
    used_bytes: int
    limit_bytes: int
    used_percent: float
    file_count: int


class DashboardResponse(BaseModel):
    conversion_stats: ConversionStats
    storage_stats: StorageStats
    credit_balance: int
    tier: str
    recent_jobs_count: int
    active_api_keys: int
