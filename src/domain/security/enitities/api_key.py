from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional

class APIKeyStatus(StrEnum):
    """Represents the status of an API key.

    Values match the PostgreSQL ``apikeystatus`` enum created in
    migration a23fe025bb21.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    REVOKED = "REVOKED"

@dataclass
class APIKey:
    """Represents an API key entity."""

    id: str
    key: str #hashed version in storage
    user_id: str
    name: str
    status: APIKeyStatus
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    rate_limit_per_usage: int = 100

    def is_valid(self) -> bool:
        """Checks if the API key is valid based on its status and expiration."""
        if self.status != APIKeyStatus.ACTIVE:
            return False
        if self.expires_at:
            # Normalise naive datetimes (e.g. SQLite) to aware for comparison.
            expires = self.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if datetime.now(UTC) > expires:
                return False
        return True
