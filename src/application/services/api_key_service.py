from typing import Optional
import secrets
import hashlib
from datetime import datetime, timedelta
from src.application.ports.database_port import APIKeyRepositoryPort
from src.domain.security.enitities.api_key import APIKey, APIKeyStatus

class APIKeyService: