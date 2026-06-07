from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    # Redis configuration
    REDIS_URL: SecretStr
    REDIS_CACHE_TTL: int = 86400  # Cache time-to-live in seconds

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings() #type: ignore
