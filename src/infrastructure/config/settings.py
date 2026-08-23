from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):

    # Environment: "development" | "production"
    ENVIRONMENT: str = "development"

    SECRET_KEY: SecretStr
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Run database migrations at startup. Disable in deployments that run
    # migrations as a separate one-shot step to avoid concurrent upgrades.
    RUN_MIGRATIONS: bool = True

    # Redis configuration
    REDIS_URL: SecretStr
    REDIS_CACHE_TTL: int = 86400  # Cache time-to-live in seconds

    # Database configuration
    DATABASE_URL: SecretStr

    # S3 / Object Storage
    BACKBLAZE_ENDPOINT: str
    BACKBLAZE_ACCESS_KEY: SecretStr
    BACKBLAZE_SECRET_KEY: SecretStr
    S3_BUCKET_NAME: str = "transform-convertion-bucket"
    BASE_TARGET_KEY: str
    UPLOAD_URL_TTL_MINUTES: int = 15
    BACKBLAZE_USE_SSL: bool = False  # False for local Minio (http), True for AWS S3/B2 (https)

    # Encryption
    ENCRYPTION_MASTER_KEY: SecretStr | None = None  # Fernet key for file encryption

    # Stripe
    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    STRIPE_PRICE_PRO: str = "price_pro_monthly"
    STRIPE_PRICE_PRO_PLUS: str = "price_pro_plus_monthly"
    STRIPE_PRICE_ENTERPRISE: str = "price_enterprise_monthly"
    STRIPE_SUCCESS_URL: str = "http://localhost:5173/app/billing?checkout=success"
    STRIPE_CANCEL_URL: str = "http://localhost:5173/app/billing?checkout=cancelled"
    STRIPE_CREDIT_SUCCESS_URL: str = "http://localhost:5173/app/billing?credits=success"
    STRIPE_CREDIT_CANCEL_URL: str = "http://localhost:5173/app/billing?credits=cancelled"
    STRIPE_PORTAL_RETURN_URL: str = "http://localhost:5173/app/billing"

    # Rate Limiting (requests per minute)
    RATE_LIMIT_GUEST: int = 10
    RATE_LIMIT_FREE: int = 30
    RATE_LIMIT_PRO: int = 100
    RATE_LIMIT_PRO_PLUS: int = 200
    RATE_LIMIT_ENTERPRISE: int = 500
    RATE_LIMIT_API_KEY_DEFAULT: int = 1000
    RATE_LIMIT_AUTH: int = 10  # stricter window for login/register/refresh
    # Limit for authenticated (JWT) users — higher than the IP-based free limit
    # so real app usage (many API calls from the SPA) is not throttled.
    RATE_LIMIT_AUTHENTICATED: int = 600

    # CORS
    # ISO 27001 A.8.26: production must use explicit origins; wildcard with
    # credentials is rejected by validation. Default to empty (no CORS).
    # For local dev, include the Vite dev server and the API origin.
    ALLOWED_ORIGINS: list[str] = []

    # Backblaze B2 / S3 bucket CORS allowed origins. These are the SPA origins
    # permitted to make cross-origin PUT (upload) requests directly to object
    # storage. Applied to the bucket at startup when configured.
    S3_CORS_ALLOWED_ORIGINS: list[str] = []

    # Monitoring
    SENTRY_DSN: SecretStr | None = None

    # Frontend (SPA) static serving
    # Absolute or project-relative path to the built frontend `dist` folder.
    # When None, the frontend is not served by the API (frontend runs separately).
    FRONTEND_DIST_DIR: str | None = "web/dist"

    # File size limits (bytes)
    GUEST_MAX_FILE_SIZE: int = 50 * 1024 * 1024     # 50MB
    FREE_MAX_FILE_SIZE: int = 100 * 1024 * 1024      # 100MB
    PRO_MAX_FILE_SIZE: int = 500 * 1024 * 1024       # 500MB
    PRO_PLUS_MAX_FILE_SIZE: int = 1024 * 1024 * 1024  # 1GB

    # Worker
    WORKER_CONSUMER_GROUP: str = "conversion-workers"
    WORKER_BATCH_SIZE: int = 10
    """Batch size for worker queue consumption. Larger batches reduce queue overhead but increase memory usage."""
    WORKER_CONVERSION_TIMEOUT: int = 600  # 10 minutes

    # Cleanup worker (guest data retention)
    CLEANUP_INTERVAL_SECONDS: int = 6 * 60 * 60  # every 6 hours
    GUEST_JOB_RETENTION_HOURS: int = 24          # ownerless conversion jobs
    GUEST_FILE_RETENTION_HOURS: int = 24         # guest uploads with expires_at
    TEMP_FILE_RETENTION_HOURS: int = 1           # temp/ prefix objects
    JOB_ARCHIVE_AFTER_DAYS: int = 30             # full job history retention

    # Credit calculation — time-based, derived from worker compute seconds
    # Formula: credits_used = ceil(compute_seconds * CREDIT_BASE_RATE_PER_SECOND * format_multiplier)
    CREDIT_BASE_RATE_PER_SECOND: float = 0.5  # 0.5 credits per second of raw compute

    # Format complexity multipliers (higher = more expensive per second of compute)
    CREDIT_MULTIPLIER_DOCUMENT: float = 1.0   # PDF/DOCX — baseline
    CREDIT_MULTIPLIER_AUDIO: float = 1.2       # ffmpeg transcoding
    CREDIT_MULTIPLIER_VIDEO: float = 2.0       # ffmpeg video — heavy
    CREDIT_MULTIPLIER_IMAGE: float = 0.8       # lightweight Pillow ops
    CREDIT_MULTIPLIER_EBOOK: float = 1.5       # calibre conversions
    CREDIT_MULTIPLIER_ARCHIVE: float = 0.6     # simple re-packaging

    # Tier discount factors (multiply credits_used by this)
    CREDIT_DISCOUNT_GUEST: float = 1.0    # no discount
    CREDIT_DISCOUNT_FREE: float = 1.0     # no discount
    CREDIT_DISCOUNT_PRO: float = 0.8      # 20% off
    CREDIT_DISCOUNT_PRO_PLUS: float = 0.6  # 40% off
    CREDIT_DISCOUNT_ENTERPRISE: float = 0.5  # 50% off

    model_config = SettingsConfigDict(env_file=".env")

    # Secrets that must never be used outside local development.
    _INSECURE_SECRETS = {
        "change-me-to-a-random-secret-key",
        "secret",
        "changeme",
        "your-secret-key",
    }

    def validate(self) -> None:
        """Fail fast at startup when the configuration is unsafe for production."""
        environment = self.ENVIRONMENT.strip().lower()
        if environment == "production":
            secret = self.SECRET_KEY.get_secret_value()
            if secret in self._INSECURE_SECRETS or len(secret) < 32:
                raise RuntimeError(
                    "SECRET_KEY must be a strong random secret (>=32 chars) in production."
                )
            if "*" in self.ALLOWED_ORIGINS:
                raise RuntimeError(
                    "ALLOWED_ORIGINS must not contain '*' when credentials are allowed "
                    "(CORS + credentials requires explicit origins) in production."
                )
            if self.BACKBLAZE_USE_SSL is False:
                raise RuntimeError(
                    "BACKBLAZE_USE_SSL must be true for object storage in production."
                )
            if self.ENCRYPTION_MASTER_KEY is None:
                # ISO 27001 A.8.24 / A.8.25: at-rest encryption is a production
                # requirement. Refuse to start with plaintext object storage.
                raise RuntimeError(
                    "ENCRYPTION_MASTER_KEY must be configured in production to "
                    "encrypt files at rest."
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate()
    return settings
