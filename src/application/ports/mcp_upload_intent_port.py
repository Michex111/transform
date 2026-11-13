"""Persistence port for MCP upload intents.

Declared as a narrow protocol rather than depending on the SQL adapter so the
one write that has to be atomic — ``start_receiving`` — is visible in a single
place, and so a test can substitute an in-memory implementation without a
database.

The atomic redemption is the security-critical operation: two concurrent
requests holding the same capability must not both receive the upload. In SQL
that is one conditional ``UPDATE ... WHERE used_at IS NULL AND expires_at >
:now``; the port keeps that shape rather than exposing a read-then-write pair
that a caller could get wrong.
"""

from datetime import datetime
from typing import Protocol

from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntent


class MCPUploadIntentRepositoryPort(Protocol):
    async def create(self, intent: MCPUploadIntent) -> MCPUploadIntent: ...

    async def get(self, intent_id: str) -> MCPUploadIntent | None: ...

    async def start_receiving(
        self, intent_id: str, *, now: datetime
    ) -> MCPUploadIntent | None:
        """Atomically move ``PENDING`` -> ``RECEIVING``, exactly once.

        Returns the updated intent, or ``None`` when the row is unknown, was
        already redeemed, is past ``expires_at``, or is in any state other than
        ``PENDING``. The caller must treat ``None`` as "unavailable" and never
        as "retry".
        """
        ...

    async def mark_completed(
        self, intent_id: str, *, file_id: str, job_id: str | None
    ) -> None: ...

    async def mark_failed(self, intent_id: str, *, error: str) -> None: ...

    async def delete_expired(self, before: datetime) -> int: ...
