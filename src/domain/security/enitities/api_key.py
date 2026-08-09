from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional, Set

class APIKeyStatus(StrEnum):
    """Represents the status of an API key."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"

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
        if self.expires_at and datetime.now() > self.expires_at:
            return False
        return True
