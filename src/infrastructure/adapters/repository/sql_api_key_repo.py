"""SQLAlchemy repository for API keys."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.security.enitities.api_key import APIKey
from src.infrastructure.database.models import APIKeyModel


class SQLAPIKeyRepository:
    """Persists API keys in PostgreSQL."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, api_key: APIKey) -> None:
        """Insert a new API key row."""
        self._session.add(
            APIKeyModel(
                id=api_key.id,
                key=api_key.key,
                user_id=int(api_key.user_id),
                name=api_key.name,
                status=api_key.status,
                created_at=api_key.created_at or datetime.now(UTC),
                expires_at=api_key.expires_at,
                last_used_at=api_key.last_used_at,
                rate_limit_per_usage=api_key.rate_limit_per_usage,
            )
        )
        await self._session.commit()

    async def get_by_id(self, api_key_id: str) -> APIKey | None:
        """Fetch an API key row by its primary key."""
        result = await self._session.execute(
            select(APIKeyModel).where(APIKeyModel.id == api_key_id)
        )
        return self._to_entity(result.scalar_one_or_none())

    async def find_by_key(self, key_hash: str) -> APIKey | None:
        """Fetch an API key row by its stored hash."""
        result = await self._session.execute(
            select(APIKeyModel).where(APIKeyModel.key == key_hash)
        )
        return self._to_entity(result.scalar_one_or_none())

    async def find_by_user(self, user_id: int) -> list[APIKey]:
        """List all API keys belonging to a user, newest first."""
        result = await self._session.execute(
            select(APIKeyModel)
            .where(APIKeyModel.user_id == user_id)
            .order_by(APIKeyModel.created_at.desc())
        )
        rows = result.scalars().all()
        entities = [self._to_entity(r) for r in rows]
        return [e for e in entities if e is not None]

    async def update(self, api_key: APIKey) -> None:
        """Update mutable fields (status, last_used_at) of an API key."""
        await self._session.execute(
            update(APIKeyModel)
            .where(APIKeyModel.id == api_key.id)
            .values(
                status=api_key.status,
                last_used_at=api_key.last_used_at,
                expires_at=api_key.expires_at,
            )
        )
        await self._session.commit()

    async def touch_last_used(self, api_key_id: str) -> None:
        """Record usage time for an API key."""
        await self._session.execute(
            update(APIKeyModel)
            .where(APIKeyModel.id == api_key_id)
            .values(last_used_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def delete(self, api_key_id: str) -> bool:
        """Permanently remove an API key. Returns True if a row was removed."""
        result = await self._session.execute(
            delete(APIKeyModel).where(APIKeyModel.id == api_key_id)
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    def _to_entity(self, model: APIKeyModel | None) -> APIKey | None:
        if model is None:
            return None
        return APIKey(
            id=model.id,
            key=model.key,
            user_id=str(model.user_id),
            name=model.name,
            status=model.status,
            created_at=model.created_at,
            expires_at=model.expires_at,
            last_used_at=model.last_used_at,
            rate_limit_per_usage=model.rate_limit_per_usage,
        )
