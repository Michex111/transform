"""User dashboard API endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from src.domain.security.enitities.api_key import APIKeyStatus
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_api_key_repository,
    get_conversion_repository,
    get_credit_repository,
    get_subscription_repository,
    get_user_file_repository,
)
from src.presentation.schemas.dashboard import (
    ConversionStats,
    DashboardResponse,
    StorageStats,
)
from src.presentation.schemas.subscription import domain_tier_to_api

router = APIRouter(prefix="/api/v1/user", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: CurrentUser,
    job_repo: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    file_repo: Annotated[SQLUserFileRepository, Depends(get_user_file_repository)],
    credit_repo: Annotated[SQLCreditRepository, Depends(get_credit_repository)],
    subscription_repo: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
    api_key_repo: Annotated[SQLAPIKeyRepository, Depends(get_api_key_repository)],
) -> DashboardResponse:
    """Get the user's dashboard with conversion stats, storage, and credit info."""
    tier = await subscription_repo.get_tier_for_user(current_user.id)
    policy = TierPolicy.for_tier(tier)

    counts = await job_repo.count_by_status(current_user.id)
    credits_used = await job_repo.sum_credits_used(current_user.id)

    used_bytes = await file_repo.get_user_storage_used(current_user.id)
    file_count = await file_repo.count_user_files(current_user.id)

    period_key = datetime.now(UTC).strftime("%Y-%m")
    credit = await credit_repo.get_credit(str(current_user.id), period_key)
    balance = credit.remaining if credit is not None else (policy.monthly_conversion_credits or 0)

    api_keys = await api_key_repo.find_by_user(current_user.id)
    active_api_keys = sum(1 for k in api_keys if k.status == APIKeyStatus.ACTIVE)

    limit_bytes = policy.storage_quota_bytes
    used_percent = round((used_bytes / limit_bytes) * 100, 2) if limit_bytes else 0.0

    return DashboardResponse(
        conversion_stats=ConversionStats(
            total_jobs=counts["TOTAL"],
            successful_jobs=counts["COMPLETED"],
            failed_jobs=counts["FAILED"],
            total_credits_used=credits_used,
        ),
        storage_stats=StorageStats(
            used_bytes=used_bytes,
            limit_bytes=limit_bytes,
            used_percent=used_percent,
            file_count=file_count,
        ),
        credit_balance=balance,
        tier=domain_tier_to_api(tier).value,
        recent_jobs_count=counts["TOTAL"],
        active_api_keys=active_api_keys,
    )


@router.get("/profile")
async def get_profile(current_user: CurrentUser):
    """Get the current user's profile."""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat() if hasattr(current_user, 'created_at') else None,
    }
