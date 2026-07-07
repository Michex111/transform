from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    # Redis configuration
    REDIS_URL: SecretStr
    REDIS_CACHE_TTL: int = 86400  # Cache time-to-live in seconds

    #Database configuration
    DATABASE_URL: SecretStr

    # s3 access
    BACKBLAZE_ENDPOINT: str
    BACKBLAZE_ACCESS_KEY: SecretStr
    BACKBLAZE_SECRET_KEY: SecretStr
    S3_BUCKET_NAME: str = "transform-convertion-bucket"
    BASE_TARGET_KEY: str
    UPLOAD_URL_TTL_MINUTES: int = 15  # Time-to-live for upload URLs in minutes

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings() #type: ignore
