from typing import Optional
import secrets
import hashlib
from datetime import datetime, timedelta, UTC
from src.application.ports.database_port import APIKeyRepositoryPort
from src.domain.security.enitities.api_key import APIKey, APIKeyStatus
import uuid


def hash_api_key(plaintext: str) -> str:
    """Hash a plaintext API key with SHA-256 for storage."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class APIKeyService:
    """
    Application service for API key lifecycle management.

    Only the SHA-256 hash of a key is ever persisted; the plaintext is
    returned exactly once at creation time.
    """

    KEY_PREFIX = "tr_"

    def __init__(self, repository: APIKeyRepositoryPort):
        self._repository = repository

    async def create(
        self,
        user_id: int,
        name: str,
        expires_in_days: int | None = 30,
        rate_limit_per_minute: int = 100,
    ) -> tuple[str, APIKey]:
        """Generate a new API key.

        Returns:
            (plaintext_key, persisted_entity). The plaintext is shown once.
        """
        raw = secrets.token_urlsafe(32)
        plaintext = f"{self.KEY_PREFIX}{raw}"

        api_key = APIKey(
            id=str(uuid.uuid4()),
            key=hash_api_key(plaintext),
            user_id=str(user_id),
            name=name,
            status=APIKeyStatus.ACTIVE,
            created_at=datetime.now(UTC),
            expires_at=(
                datetime.now(UTC) + timedelta(days=expires_in_days)
                if expires_in_days
                else None
            ),
            rate_limit_per_usage=rate_limit_per_minute,
        )
        await self._repository.save(api_key)
        return plaintext, api_key

    async def list_for_user(self, user_id: int) -> list[APIKey]:
        """List all keys owned by a user (hashes only)."""
        return await self._repository.find_by_user(user_id)

    async def revoke(self, api_key_id: str, user_id: int) -> APIKey | None:
        """Mark a key as revoked. Returns None when not found or not owned."""
        api_key = await self._repository.get_by_id(api_key_id)
        if api_key is None or int(api_key.user_id) != user_id:
            return None
        if api_key.status != APIKeyStatus.REVOKED:
            api_key.status = APIKeyStatus.REVOKED
            await self._repository.update(api_key)
        return api_key

    async def delete(self, api_key_id: str, user_id: int) -> bool:
        """Permanently remove a key owned by the user."""
        api_key = await self._repository.get_by_id(api_key_id)
        if api_key is None or int(api_key.user_id) != user_id:
            return False
        return await self._repository.delete(api_key_id)

    async def authenticate(self, plaintext_key: str) -> Optional[APIKey]:
        """Resolve and validate an API key from its plaintext form."""
        api_key = await self._repository.find_by_key(hash_api_key(plaintext_key))
        if api_key is None or not api_key.is_valid():
            return None
        await self._repository.touch_last_used(api_key.id)
        return api_key
