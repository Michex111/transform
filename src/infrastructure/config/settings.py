from functools import lru_cache
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


logger = logging.getLogger(__name__)


# The only environment names the application supports (see .env.example and
# README). Any other value is rejected at boot so a typo like ``prod`` cannot
# silently disable the production safety checks.
_SUPPORTED_ENVIRONMENTS = frozenset({"development", "production"})


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

    # ------------------------------------------------------------------
    # Transactional email
    # ------------------------------------------------------------------
    # How verification emails leave the process. ``auto`` (the default) picks a
    # real transport as soon as one is configured — Resend when RESEND_API_KEY
    # is set, SMTP when SMTP_HOST is set — and otherwise falls back to the
    # console transport, which logs the message instead of sending it. ``auto``
    # means the feature works out of the box in local development and starts
    # really sending the moment credentials are added, with no extra switch to
    # remember.
    #
    # Accepted values: auto | console | smtp | resend. A value that names a
    # transport whose credentials are missing is a configuration error and is
    # rejected in ``validate()`` rather than silently degrading — an explicit
    # choice must not be quietly ignored. ``auto`` is never rejected, because
    # its whole purpose is to resolve to whatever is available.
    EMAIL_BACKEND: str = "auto"

    # Envelope sender. Must be a domain you control and have verified with your
    # provider, or the provider will reject the send and (for Gmail-class
    # mailboxes) the message lands in spam.
    EMAIL_FROM_ADDRESS: str = "no-reply@example.com"
    EMAIL_FROM_NAME: str = "Transform"
    # Optional Reply-To. Left unset by default: a no-reply sender with a
    # monitored reply-to is a common deliverability pattern, but pointing it at
    # an unmonitored address is worse than omitting it.
    EMAIL_REPLY_TO: str | None = None

    # SMTP transport (EMAIL_BACKEND=smtp). Works with any provider that speaks
    # SMTP — SendGrid, Postmark, Mailgun, Amazon SES, Google Workspace.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    # STARTTLS (upgrade a plaintext connection, port 587) vs implicit TLS
    # (port 465). Exactly one of the two is normally enabled.
    SMTP_USE_STARTTLS: bool = True
    SMTP_USE_SSL: bool = False
    # Bounds a stalled provider so a slow SMTP host cannot pin a request thread.
    SMTP_TIMEOUT_SECONDS: int = 15

    # Resend transport (EMAIL_BACKEND=resend).
    RESEND_API_KEY: SecretStr | None = None
    RESEND_API_URL: str = "https://api.resend.com/emails"
    EMAIL_HTTP_TIMEOUT_SECONDS: int = 15

    # Public origin of the SPA, used to build the links in outbound email
    # (e.g. ``https://transform-web.onrender.com``). Must NOT include a
    # trailing slash or the `/app` suffix — it is joined with a route path.
    APP_BASE_URL: str = "http://localhost:5173"

    # How long a verification link stays valid. Bounded on the low side by how
    # long a user realistically takes to open their inbox, and on the high side
    # by how long a leaked mailbox/URL stays exploitable.
    EMAIL_VERIFICATION_TTL_HOURS: int = 24

    # Minimum gap between two verification emails for the same account. Stops
    # the resend endpoint from being usable as a mail-bomb against a third
    # party's inbox (and from burning the provider's sending quota).
    EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60

    # Master switch for the verification gate. When true an unverified account
    # cannot sign in. This exists as an operational safety valve: if the email
    # provider is down or misconfigured, turning this off restores sign-in for
    # a backlog of legitimately-registered users immediately (an env change plus
    # a restart) instead of leaving every new signup locked out until the
    # provider is fixed. Accounts that later verify are unaffected either way.
    EMAIL_VERIFICATION_REQUIRED: bool = True

    # Frontend (SPA) static serving
    # DEPRECATED / NO-OP: the API no longer serves the SPA. The React app is
    # built and hosted as a separate static site (Render static site
    # `transform-web`) and reaches this API cross-origin. The field is retained
    # only so an existing deployment that still sets FRONTEND_DIST_DIR in its
    # environment does not crash the boot (Settings uses `extra="forbid"`); its
    # value is never read. It defaults to None and should not be set.
    FRONTEND_DIST_DIR: str | None = None

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
    QUEUE_STREAM_PREFIX: str = ""
    """Namespace for every Redis stream/queue key (default: none).

    Development and production share ONE Redis instance, so without a prefix a
    single consumer group competes for every message: a job created in one
    environment can be executed by a worker bound to the other environment's
    database. That fails silently — the worker converts, uploads, ACKs and logs
    success while the owning database's row never advances (the status UPDATE
    matches no row and raises nothing), and credits are consumed against the
    wrong environment's account.

    Set to e.g. ``dev:`` in a development ``.env`` so dev publishes and
    consumes ``dev:conversion_jobs:*`` while production keeps the unprefixed
    names. Leave EMPTY in production: an empty prefix reproduces the original
    stream names exactly, so this setting is a no-op there.
    """

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

    _SUPPORTED_EMAIL_BACKENDS = frozenset({"auto", "console", "smtp", "resend"})

    def _resolve_email_backend(self) -> str:
        """The concrete email transport to use: console | smtp | resend.

        ``auto`` prefers a real transport over a discard transport, checking
        Resend first (an API key is a deliberate, single-purpose credential)
        before SMTP. Callers should use this rather than reading
        ``EMAIL_BACKEND`` directly so the resolution rule lives in one place.
        """
        configured = self.EMAIL_BACKEND.strip().lower()
        if configured != "auto":
            return configured
        if self.RESEND_API_KEY is not None:
            return "resend"
        if self.SMTP_HOST:
            return "smtp"
        return "console"

    def validate(self) -> None:
        """Fail fast at startup when the configuration is unsafe for production."""
        environment = self.ENVIRONMENT.strip().lower()

        # Email transport. An explicit choice is honoured or rejected — never
        # silently downgraded to the console sink, because that would drop real
        # verification emails for real users while every health check stayed
        # green. ``auto`` is exempt: it exists precisely to fall back.
        email_backend = self.EMAIL_BACKEND.strip().lower()
        if email_backend not in self._SUPPORTED_EMAIL_BACKENDS:
            raise RuntimeError(
                f"Unsupported EMAIL_BACKEND {self.EMAIL_BACKEND!r}; expected one of "
                f"{sorted(self._SUPPORTED_EMAIL_BACKENDS)}."
            )
        if email_backend == "resend" and self.RESEND_API_KEY is None:
            raise RuntimeError("EMAIL_BACKEND=resend requires RESEND_API_KEY.")
        if email_backend == "smtp" and not self.SMTP_HOST:
            raise RuntimeError("EMAIL_BACKEND=smtp requires SMTP_HOST.")
        if self.SMTP_USE_SSL and self.SMTP_USE_STARTTLS:
            # Both at once means "connect with TLS, then upgrade to TLS".
            raise RuntimeError(
                "SMTP_USE_SSL and SMTP_USE_STARTTLS are mutually exclusive; "
                "use SMTP_USE_SSL for port 465 or SMTP_USE_STARTTLS for port 587."
            )
        if self.EMAIL_VERIFICATION_TTL_HOURS <= 0:
            raise RuntimeError("EMAIL_VERIFICATION_TTL_HOURS must be positive.")

        if environment not in _SUPPORTED_ENVIRONMENTS:
            # Fail closed: an unrecognised value (e.g. "prod") must not silently
            # skip the production checks below.
            raise RuntimeError(
                f"Unsupported ENVIRONMENT {self.ENVIRONMENT!r}; expected one of "
                f"{sorted(_SUPPORTED_ENVIRONMENTS)}."
            )
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

        self._warn_on_degraded_email_delivery()

    def _warn_on_degraded_email_delivery(self) -> None:
        """Log loudly when verification email cannot actually be delivered.

        Deliberately a WARNING rather than a startup failure. A hard failure
        here would turn "credentials not added yet" into a total API outage
        (``RUN_MIGRATIONS``-style startup abort), which is a far worse outcome
        than a degraded feature — and the fix is an env var, which the operator
        may not be able to set at the moment the deploy lands.

        The consequence of the degraded mode is bounded and self-healing:
        ``register_user`` suspends the verification gate while no real transport
        is configured, so registration and sign-in keep working exactly as they
        did before this feature existed, and full enforcement switches on by
        itself as soon as a transport is configured. Nothing is ever silently
        half-enforced.
        """
        if self._resolve_email_backend() != "console":
            if "localhost" in self.APP_BASE_URL or "127.0.0.1" in self.APP_BASE_URL:
                # Checked regardless of transport: a localhost link is broken
                # whether it is sent by Resend, SMTP, or nobody at all.
                logger.error(
                    "APP_BASE_URL is %r, so verification links would point at the "
                    "developer's machine and be unusable for real users. Set it to "
                    "the public SPA origin (e.g. https://transform-web.onrender.com).",
                    self.APP_BASE_URL,
                )
            return

        if self.EMAIL_VERIFICATION_REQUIRED:
            logger.error(
                "Email delivery is not configured (EMAIL_BACKEND=%s), so email "
                "verification is SUSPENDED: new accounts are still marked "
                "unverified and no verification email can be sent, but sign-in is "
                "NOT blocked, because blocking it would lock out every new signup "
                "with no way to recover. Set RESEND_API_KEY (or SMTP_HOST + SMTP_* "
                "/ EMAIL_BACKEND=smtp) to enable real delivery and enforcement.",
                self.EMAIL_BACKEND,
            )
        else:
            logger.warning(
                "Email delivery is not configured (EMAIL_BACKEND=%s); verification "
                "emails will be logged, not sent.",
                self.EMAIL_BACKEND,
            )

        if "localhost" in self.APP_BASE_URL or "127.0.0.1" in self.APP_BASE_URL:
            logger.error(
                "APP_BASE_URL is %r, so verification links would point at the "
                "developer's machine and be unusable for real users. Set it to the "
                "public SPA origin (e.g. https://transform-web.onrender.com).",
                self.APP_BASE_URL,
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.validate()
    return settings
