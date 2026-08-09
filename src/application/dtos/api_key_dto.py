from dataclasses import dataclass
from typing import Optional

@dataclass
class APIKeyCreationRequest:
    """Represents a request to create a new API key."""

    user_id: str
    name: str
    expires_in_days: Optional[int] = 30
    rate_limit_per_minute: Optional[int] = 100