from dataclasses import dataclass
from hashlib import sha256

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class SdkClientPrincipal:
    """Authenticated SDK client information derived from API key."""

    api_key: str
    tier: SubscriptionTier
    actor_key: str


def _parse_sdk_keys() -> dict[str, SubscriptionTier]:
    settings = get_settings()
    default_tier = SubscriptionTier(settings.SDK_DEFAULT_TIER.lower())
    if not settings.SDK_API_KEYS.strip():
        return {}
    parsed: dict[str, SubscriptionTier] = {}
    for item in settings.SDK_API_KEYS.split(","):
        entry = item.strip()
        if not entry:
            continue
        if ":" in entry:
            key, tier_raw = entry.split(":", maxsplit=1)
            tier = SubscriptionTier(tier_raw.strip().lower())
            parsed[key.strip()] = tier
            continue
        parsed[entry] = default_tier
    return parsed


def _actor_key_for_api_key(api_key: str) -> str:
    digest = sha256(api_key.encode("utf-8")).hexdigest()[:24]
    return f"sdk:{digest}"


async def get_sdk_client(
    api_key: str | None = Depends(api_key_header),
) -> SdkClientPrincipal:
    """Validates SDK API key and returns SDK client identity."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
        )

    allowed = _parse_sdk_keys()
    tier = allowed.get(api_key)
    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return SdkClientPrincipal(
        api_key=api_key,
        tier=tier,
        actor_key=_actor_key_for_api_key(api_key),
    )
