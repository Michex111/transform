"""SQL adapter for MCP upload intents.

The only non-obvious operation is :meth:`start_receiving`. It is a single
conditional ``UPDATE`` rather than a read-then-write pair: two concurrent
redemptions of the same capability must not both win, and the row predicate
(``used_at IS NULL AND expires_at > :now AND status = 'PENDING'``) is what makes
that true under SQLite and PostgreSQL alike. Its ``rowcount`` is the authority —
if it is not exactly one row, the caller refuses with the generic
"unavailable" answer.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.security.enitities.mcp_upload_intent import (
    MCPUploadIntent,
    MCPUploadIntentStatus,
)
from src.infrastructure.database.models import MCPUploadIntentModel

#: Statuses a housekeeping sweep may remove once their window has elapsed.
#: ``COMPLETED`` is deliberately kept: it is the record that an upload became a
#: Drive file (and the link from a capability to a conversion job), it holds no
#: secret, and keeping it is what lets ``get_upload_status`` still answer for a
#: completed upload after the link itself has expired. ``RECEIVING`` is not
#: protected: a transfer that never finished must be collectable, otherwise a
#: dead client leaves a row forever.
_PURGEABLE = (
    MCPUploadIntentStatus.PENDING.value,
    MCPUploadIntentStatus.RECEIVING.value,
    MCPUploadIntentStatus.FAILED.value,
    MCPUploadIntentStatus.EXPIRED.value,
)


def _aware(value: datetime | None) -> datetime | None:
    """Normalise SQLite's naive datetimes to UTC-aware ones."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SQLMCPUploadIntentRepository:
    """Persistence for the MCP upload-intent table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, intent: MCPUploadIntent) -> MCPUploadIntent:
        self._session.add(
            MCPUploadIntentModel(
                id=intent.id,
                user_id=intent.user_id,
                grant_id=intent.grant_id,
                client_id=intent.client_id,
                file_name=intent.file_name,
                extension=intent.extension,
                max_bytes=intent.max_bytes,
                folder_id=intent.folder_id,
                target_format=intent.target_format,
                status=intent.status.value,
                file_id=intent.file_id,
                job_id=intent.job_id,
                error=intent.error,
                created_at=intent.created_at or datetime.now(UTC),
                expires_at=intent.expires_at,
                used_at=intent.used_at,
            )
        )
        await self._session.commit()
        stored = await self.get(intent.id)
        assert stored is not None  # just written
        return stored

    async def get(self, intent_id: str) -> MCPUploadIntent | None:
        row = await self._session.get(MCPUploadIntentModel, intent_id)
        return self._to_intent(row)

    async def start_receiving(
        self, intent_id: str, *, now: datetime
    ) -> MCPUploadIntent | None:
        result = await self._session.execute(
            update(MCPUploadIntentModel)
            .where(
                MCPUploadIntentModel.id == intent_id,
                MCPUploadIntentModel.used_at.is_(None),
                MCPUploadIntentModel.expires_at > now,
                MCPUploadIntentModel.status == MCPUploadIntentStatus.PENDING.value,
            )
            .values(
                status=MCPUploadIntentStatus.RECEIVING.value,
                used_at=now,
            )
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            await self._session.rollback()
            return None
        await self._session.commit()
        return await self.get(intent_id)

    async def mark_completed(
        self, intent_id: str, *, file_id: str, job_id: str | None
    ) -> None:
        await self._session.execute(
            update(MCPUploadIntentModel)
            .where(MCPUploadIntentModel.id == intent_id)
            .values(
                status=MCPUploadIntentStatus.COMPLETED.value,
                file_id=file_id,
                job_id=job_id,
                error=None,
            )
        )
        await self._session.commit()

    async def mark_failed(self, intent_id: str, *, error: str) -> None:
        await self._session.execute(
            update(MCPUploadIntentModel)
            .where(MCPUploadIntentModel.id == intent_id)
            .values(
                status=MCPUploadIntentStatus.FAILED.value,
                error=error,
            )
        )
        await self._session.commit()

    async def delete_expired(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(MCPUploadIntentModel).where(
                MCPUploadIntentModel.expires_at < before,
                MCPUploadIntentModel.status.in_(_PURGEABLE),
            )
        )
        await self._session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_for_user(self, user_id: int, *, limit: int = 20) -> list[MCPUploadIntent]:
        """Newest-first intents for one user (used by tests and diagnostics)."""
        result = await self._session.execute(
            select(MCPUploadIntentModel)
            .where(MCPUploadIntentModel.user_id == user_id)
            .order_by(MCPUploadIntentModel.created_at.desc())
            .limit(limit)
        )
        return [
            intent
            for row in result.scalars().all()
            if (intent := self._to_intent(row)) is not None
        ]

    @staticmethod
    def _to_intent(row: MCPUploadIntentModel | None) -> MCPUploadIntent | None:
        if row is None:
            return None
        return MCPUploadIntent(
            id=row.id,
            user_id=row.user_id,
            grant_id=row.grant_id,
            client_id=row.client_id,
            file_name=row.file_name,
            extension=row.extension,
            max_bytes=row.max_bytes,
            folder_id=row.folder_id,
            target_format=row.target_format,
            status=MCPUploadIntentStatus.coerce(row.status),
            file_id=row.file_id,
            job_id=row.job_id,
            error=row.error,
            created_at=_aware(row.created_at),
            expires_at=_aware(row.expires_at),
            used_at=_aware(row.used_at),
        )
