"""In-memory ``MCPUploadIntentRepositoryPort`` for unit tests.

Mirrors the SQL adapter's contract, including the single-use predicate on
``start_receiving``: the second call returns ``None``. It is intentionally
synchronous inside (no ``await`` between the check and the write) so a test can
assert the atomic transition without a database.
"""

from dataclasses import replace
from datetime import UTC, datetime

from src.domain.security.enitities.mcp_upload_intent import (
    MCPUploadIntent,
    MCPUploadIntentStatus,
)


class FakeMCPUploadIntentRepository:
    def __init__(self) -> None:
        self.intents: dict[str, MCPUploadIntent] = {}
        self.created: list[MCPUploadIntent] = []

    async def create(self, intent: MCPUploadIntent) -> MCPUploadIntent:
        stored = replace(intent, created_at=intent.created_at or datetime.now(UTC))
        self.intents[stored.id] = stored
        self.created.append(stored)
        return stored

    async def get(self, intent_id: str) -> MCPUploadIntent | None:
        return self.intents.get(intent_id)

    async def start_receiving(
        self, intent_id: str, *, now: datetime
    ) -> MCPUploadIntent | None:
        intent = self.intents.get(intent_id)
        if intent is None:
            return None
        if intent.status is not MCPUploadIntentStatus.PENDING:
            return None
        if intent.used_at is not None or intent.expires_at is None or intent.expires_at <= now:
            return None
        intent.status = MCPUploadIntentStatus.RECEIVING
        intent.used_at = now
        return intent

    async def mark_completed(
        self, intent_id: str, *, file_id: str, job_id: str | None
    ) -> None:
        intent = self.intents.get(intent_id)
        if intent is None:
            return
        intent.status = MCPUploadIntentStatus.COMPLETED
        intent.file_id = file_id
        intent.job_id = job_id
        intent.error = None

    async def mark_failed(self, intent_id: str, *, error: str) -> None:
        intent = self.intents.get(intent_id)
        if intent is None:
            return
        intent.status = MCPUploadIntentStatus.FAILED
        intent.error = error

    async def delete_expired(self, before: datetime) -> int:
        expired = [
            key
            for key, intent in self.intents.items()
            if intent.expires_at is not None
            and intent.expires_at < before
            and intent.status is not MCPUploadIntentStatus.COMPLETED
        ]
        for key in expired:
            del self.intents[key]
        return len(expired)
