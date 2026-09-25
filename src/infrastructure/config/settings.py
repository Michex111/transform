from functools import lru_cache
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


logger = logging.getLogger(__name__)


# The only environment names the application supports (see .env.example and
# README). Any other value is rejected at boot so a typo like ``prod`` cannot
# silently disable the production safety checks.
_SUPPORTED_ENVIRONMENTS = frozenset({"development", "production"})

# The single authoritative ceiling for a single authenticated upload. Every
# per-tier cap defaults to this (and ``validate()`` refuses to start if a tier
# is configured above it), so "what can the store actually accept" is expressed
# in exactly one place rather than repeated per tier.
#
# 5 GiB is also the *single-PUT* ceiling for Backblaze B2 and AWS S3: a
# presigned PUT above it is rejected by the provider, and a real 5 GiB transfer
# outlives a 15-minute URL on ordinary connections. Crossing
# ``MULTIPART_THRESHOLD_BYTES`` therefore switches the session to S3 multipart
# upload (see ``TransferService``), which is what makes this ceiling reachable.
_MAX_UPLOAD_FILE_SIZE_CEILING: int = 5 * 1024 * 1024 * 1024  # 5 GiB

# S3/S3-compatible providers cap a multipart upload at 10 000 parts; exceeding
# it fails only at the END of a multi-gigabyte transfer. ``validate()`` asserts
# the configured part size keeps the ceiling under the limit.
_S3_MAX_MULTIPART_PARTS: int = 10_000


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
    # TTL for the single-PUT presigned URL (and for small upload sessions).
    # Kept at 15 minutes on purpose: it must stay short enough that a leaked URL
    # is not a lasting write primitive, and it is only used for uploads below
    # MULTIPART_THRESHOLD_BYTES. Uploads at/above that threshold get
    # LARGE_UPLOAD_URL_TTL_MINUTES instead (see the upload limits section).
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

    # How long a password-reset link stays valid. Materially shorter than the
    # verification link on purpose: a reset token is not an address proof, it
    # is a full account-takeover credential (following it sets a new password
    # with no further authentication), so its exploitable window is kept small.
    PASSWORD_RESET_TTL_MINUTES: int = 60

    # Minimum gap between two reset emails for the same account. Same reasoning
    # as the verification cooldown: without it, `forgot-password` is a mail-bomb
    # aimed at a third party's inbox — and it is unauthenticated, so anyone can
    # aim it.
    PASSWORD_RESET_RESEND_COOLDOWN_SECONDS: int = 60

    # Master switch for the verification gate. When true an unverified account
    # cannot sign in. This exists as an operational safety valve: if the email
    # provider is down or misconfigured, turning this off restores sign-in for
    # a backlog of legitimately-registered users immediately (an env change plus
    # a restart) instead of leaving every new signup locked out until the
    # provider is fixed. Accounts that later verify are unaffected either way.
    EMAIL_VERIFICATION_REQUIRED: bool = True

    # ------------------------------------------------------------------
    # Transactional SMS (phone verification)
    # ------------------------------------------------------------------
    # How phone-verification codes leave the process. ``auto`` (the default)
    # resolves to Twilio as soon as all three credentials are present, and
    # otherwise falls back to the console transport, which logs the message
    # (including the code) instead of sending it.
    #
    # Accepted values: auto | console | twilio. Naming a transport whose
    # credentials are missing is a startup error — an explicit choice must not
    # be quietly downgraded to a log sink, or a deployment that *believes* it is
    # texting users would silently write codes to a log nobody reads. ``auto``
    # is never rejected, because its whole purpose is to resolve to whatever is
    # available; the boot banner says which transport won.
    SMS_BACKEND: str = "auto"

    # Twilio Programmable Messaging (SMS_BACKEND=twilio). The account SID is not
    # secret, but the auth token is, so it is a SecretStr like the other
    # provider credentials.
    TWILIO_ACCOUNT_SID: SecretStr | None = None
    TWILIO_AUTH_TOKEN: SecretStr | None = None
    # The sender in E.164 — either a Twilio number you own or a Messaging
    # Service sender. Omitting it is the most common cause of a 400 from
    # Twilio, so it is required explicitly rather than defaulted.
    TWILIO_FROM_NUMBER: str | None = None
    # Bounds a stalled provider so a slow Twilio API cannot pin a request
    # worker while the user waits for their code.
    SMS_HTTP_TIMEOUT_SECONDS: int = 15

    # How long a verification code stays valid. Bounded on the low side by SMS
    # delivery latency (carrier queues can add minutes) and on the high side by
    # how long a leaked message stays usable.
    PHONE_VERIFICATION_TTL_MINUTES: int = 10

    # Minimum gap between two codes for the same account. Stops the resend
    # endpoint from being an SMS bomb — which costs the operator real money per
    # message, unlike a resend email — and stops it being a probe.
    PHONE_VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60

    # Failed guesses a code tolerates before it is burned and the user must
    # request a new one. This is the only defence a 6-digit code has against
    # online brute force (~20 bits of entropy), so it is not optional.
    PHONE_VERIFICATION_MAX_ATTEMPTS: int = 5

    # NOTE: there is deliberately NO ``PHONE_VERIFICATION_REQUIRED`` and no
    # sign-in gate on the phone number, unlike email. Email is the
    # account-recovery channel, so proving it is worth a lockout risk; a phone
    # number is not. Gating sign-in on it would create a lockout with no
    # recovery path (the operator's SMS provider going down would lock every
    # user out) and no benefit. Phone verification is opt-in from the settings
    # page. See ``domain/security/enitities/phone_verification.py``.

    # Frontend (SPA) static serving
    # DEPRECATED / NO-OP: the API no longer serves the SPA. The React app is
    # built and hosted as a separate static site (Render static site
    # `transform-web`) and reaches this API cross-origin. The field is retained
    # only so an existing deployment that still sets FRONTEND_DIST_DIR in its
    # environment does not crash the boot (Settings uses `extra="forbid"`); its
    # value is never read. It defaults to None and should not be set.
    FRONTEND_DIST_DIR: str | None = None

    # ------------------------------------------------------------------
    # Upload size limits (bytes)
    # ------------------------------------------------------------------
    # Two INDEPENDENT controls live here, and conflating them is the classic
    # source of "the check said it would fit" bugs:
    #
    #   * ``*_MAX_FILE_SIZE`` is the PER-FILE cap — how large one single file
    #     may be.
    #   * ``TierPolicy.storage_quota_bytes`` is the ACCOUNT quota — how much an
    #     account may hold in total.
    #
    # They are not the same number and neither implies the other. A FREE
    # account has a 5 GiB quota and a 5 GiB per-file cap, so it can legitimately
    # upload ONE 5 GiB file and then nothing more: the next upload fails the
    # quota check, not the size check. That is intended behaviour, not a bug —
    # the upload UI must show the account quota, and the pre-flight/finalize
    # checks in ``FileService`` enforce both.
    MAX_UPLOAD_FILE_SIZE_BYTES: int = _MAX_UPLOAD_FILE_SIZE_CEILING

    # Guest uploads deliberately stay at 50 MB. Guests upload through the
    # unauthenticated guest page (there is no account, no quota and no
    # attribution), so a 5 GiB guest ceiling would hand anonymous callers a
    # free 5 GiB-per-request storage/bandwidth sink. Authenticated uploads
    # through the files page use the per-tier caps below instead.
    GUEST_MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MiB
    # Authenticated per-tier per-file caps. All default to the ceiling; the
    # ENTERPRISE entry exists so it can be raised above the others later
    # without silently inheriting PRO_PLUS's value (the previous aliasing made
    # an ENTERPRISE-specific override impossible to express).
    FREE_MAX_FILE_SIZE: int = _MAX_UPLOAD_FILE_SIZE_CEILING
    PRO_MAX_FILE_SIZE: int = _MAX_UPLOAD_FILE_SIZE_CEILING
    PRO_PLUS_MAX_FILE_SIZE: int = _MAX_UPLOAD_FILE_SIZE_CEILING
    ENTERPRISE_MAX_FILE_SIZE: int = _MAX_UPLOAD_FILE_SIZE_CEILING

    # A declared file size at or above this uses S3 multipart upload (presigned
    # part URLs minted in batches); below it the session keeps the simple
    # single-PUT presigned URL. This mirrors ``S3 TransferManager``'s default
    # threshold and keeps the proven single-request path — which every existing
    # client and the whole guest flow already use — for the common case.
    MULTIPART_THRESHOLD_BYTES: int = 100 * 1024 * 1024  # 100 MiB
    # Size of each multipart part. S3 requires >= 5 MiB for every part except
    # the last, so 64 MiB is comfortably legal, and it keeps the number of
    # parts for the 5 GiB ceiling at 80 (well under the 10 000-part limit).
    # Larger parts mean fewer requests but a coarser resume granularity and
    # more memory held per part in the browser.
    MULTIPART_PART_SIZE_BYTES: int = 64 * 1024 * 1024  # 64 MiB

    # How long a multipart *session* and its part URLs stay usable. A 5 GiB
    # transfer outlives 15 minutes on ordinary consumer connections, and an
    # expired presigned URL mid-transfer is indistinguishable from a network
    # fault to the user (the browser reports a failed request, not "your URL
    # expired"), so large uploads get a materially longer window. Part URLs are
    # minted in batches, so the window only has to cover one batch plus the
    # completion call, not the whole transfer — but the session itself must
    # outlive the transfer, hence the same value for both.
    LARGE_UPLOAD_URL_TTL_MINUTES: int = 120

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

    _SUPPORTED_SMS_BACKENDS = frozenset({"auto", "console", "twilio"})

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

    def _resolve_sms_backend(self) -> str:
        """The concrete SMS transport to use: console | twilio.

        ``auto`` resolves to Twilio only when *all* of the account SID, auth
        token and from-number are set — a partial configuration would produce a
        transport that fails on every send, which is strictly worse than the
        console sink that at least reports the code. Callers should use this
        rather than reading ``SMS_BACKEND`` directly so the rule lives in one
        place.
        """
        configured = self.SMS_BACKEND.strip().lower()
        if configured != "auto":
            return configured
        if (
            self.TWILIO_ACCOUNT_SID is not None
            and self.TWILIO_AUTH_TOKEN is not None
            and self.TWILIO_FROM_NUMBER
        ):
            return "twilio"
        return "console"

    def _twilio_credentials_complete(self) -> bool:
        """True when every Twilio credential is present."""
        return (
            self.TWILIO_ACCOUNT_SID is not None
            and self.TWILIO_AUTH_TOKEN is not None
            and bool(self.TWILIO_FROM_NUMBER)
        )

    def _validate_upload_limits(self) -> None:
        """Guard the upload-size and multipart invariants.

        These are checked for EVERY environment, not just production: a
        nonsensical limit (a zero-byte cap, a part size that only fails at the
        end of a multi-gigabyte transfer) is a functional bug locally too, and
        it is much cheaper to fail at boot than to explain a mid-upload 400.
        """
        if self.MAX_UPLOAD_FILE_SIZE_BYTES <= 0:
            raise RuntimeError(
                "MAX_UPLOAD_FILE_SIZE_BYTES must be positive — a zero ceiling "
                "rejects every upload."
            )

        # The ceiling is the one number a tier may not exceed, otherwise the
        # API would advertise a per-file cap the object store cannot accept.
        tier_caps = {
            "GUEST_MAX_FILE_SIZE": self.GUEST_MAX_FILE_SIZE,
            "FREE_MAX_FILE_SIZE": self.FREE_MAX_FILE_SIZE,
            "PRO_MAX_FILE_SIZE": self.PRO_MAX_FILE_SIZE,
            "PRO_PLUS_MAX_FILE_SIZE": self.PRO_PLUS_MAX_FILE_SIZE,
            "ENTERPRISE_MAX_FILE_SIZE": self.ENTERPRISE_MAX_FILE_SIZE,
        }
        for name, value in tier_caps.items():
            if value <= 0:
                raise RuntimeError(f"{name} must be positive.")
            if value > self.MAX_UPLOAD_FILE_SIZE_BYTES:
                raise RuntimeError(
                    f"{name} ({value}) must not exceed "
                    f"MAX_UPLOAD_FILE_SIZE_BYTES "
                    f"({self.MAX_UPLOAD_FILE_SIZE_BYTES}) — a tier cannot accept "
                    "a file larger than the store's single-file ceiling."
                )

        if self.MULTIPART_PART_SIZE_BYTES <= 0:
            raise RuntimeError("MULTIPART_PART_SIZE_BYTES must be positive.")
        if self.MULTIPART_THRESHOLD_BYTES <= 0:
            raise RuntimeError("MULTIPART_THRESHOLD_BYTES must be positive.")
        if self.MULTIPART_THRESHOLD_BYTES > self.MAX_UPLOAD_FILE_SIZE_BYTES:
            # Otherwise a file between the ceiling and the threshold would be
            # neither accepted whole nor routed to multipart: a dead zone.
            raise RuntimeError(
                "MULTIPART_THRESHOLD_BYTES must not exceed "
                "MAX_UPLOAD_FILE_SIZE_BYTES; a file above the threshold must "
                "still be uploadable."
            )

        # S3 accepts at most 10 000 parts. ``ceil(ceiling / part_size)`` is the
        # number of parts the LARGEST legal upload needs; if that exceeds the
        # limit, a maximal upload would be rejected by the provider only after
        # the transfer completed. Arithmetic for the defaults:
        #   ceil(5 GiB / 64 MiB) = ceil(5368709120 / 67108864) = 80 parts.
        parts_for_ceiling = -(-self.MAX_UPLOAD_FILE_SIZE_BYTES // self.MULTIPART_PART_SIZE_BYTES)
        if parts_for_ceiling > _S3_MAX_MULTIPART_PARTS:
            required = -(-self.MAX_UPLOAD_FILE_SIZE_BYTES // _S3_MAX_MULTIPART_PARTS)
            raise RuntimeError(
                f"MULTIPART_PART_SIZE_BYTES ({self.MULTIPART_PART_SIZE_BYTES}) is "
                f"too small: the {self.MAX_UPLOAD_FILE_SIZE_BYTES}-byte ceiling "
                f"would need {parts_for_ceiling} parts, over the "
                f"{_S3_MAX_MULTIPART_PARTS}-part S3 limit. Use at least "
                f"{required} bytes."
            )

        if self.UPLOAD_URL_TTL_MINUTES <= 0:
            raise RuntimeError("UPLOAD_URL_TTL_MINUTES must be positive.")
        if self.LARGE_UPLOAD_URL_TTL_MINUTES <= 0:
            raise RuntimeError(
                "LARGE_UPLOAD_URL_TTL_MINUTES must be positive — a non-positive "
                "window issues multipart URLs that are dead on arrival."
            )

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
        if self.PASSWORD_RESET_TTL_MINUTES <= 0:
            # Fail closed at boot: a non-positive TTL issues reset links that are
            # already dead, which reads as "reset is broken" to every user while
            # every health check stays green.
            raise RuntimeError("PASSWORD_RESET_TTL_MINUTES must be positive.")

        # SMS transport. Same contract as email: an explicit choice is honoured
        # or rejected, never silently downgraded to the console sink. A
        # deployment that thinks it is texting users must not be writing codes
        # to a log instead.
        sms_backend = self.SMS_BACKEND.strip().lower()
        if sms_backend not in self._SUPPORTED_SMS_BACKENDS:
            raise ValueError(
                f"Unsupported SMS_BACKEND {self.SMS_BACKEND!r}; expected one of "
                f"{sorted(self._SUPPORTED_SMS_BACKENDS)}."
            )
        if sms_backend == "twilio" and not self._twilio_credentials_complete():
            raise RuntimeError(
                "SMS_BACKEND=twilio requires TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER."
            )
        if self.PHONE_VERIFICATION_TTL_MINUTES <= 0:
            raise RuntimeError("PHONE_VERIFICATION_TTL_MINUTES must be positive.")
        if self.PHONE_VERIFICATION_MAX_ATTEMPTS <= 0:
            raise RuntimeError(
                "PHONE_VERIFICATION_MAX_ATTEMPTS must be positive — a code with "
                "no attempt ceiling is brute-forceable in seconds."
            )

        self._validate_upload_limits()

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
        self._warn_on_degraded_sms_delivery()

    def _warn_on_degraded_sms_delivery(self) -> None:
        """Log when phone-verification codes cannot actually be delivered.

        A WARNING, not a startup failure — same reasoning as the email check.
        The consequence is bounded and self-healing: the console transport
        reports the code in the API log, so the flow still completes for a
        developer or for an operator who is watching, and the endpoint keeps
        answering honestly (202 with a log line, not a silent success). Real
        delivery switches on by itself as soon as the Twilio credentials are
        added.

        The one case that must never pass quietly is a *partial* Twilio
        configuration under ``auto``: the operator clearly intended to send real
        SMS (they added some credentials) but the transport silently fell back
        to console. That is called out explicitly, because there is no gate to
        fail open here — nothing else would surface it.
        """
        if self._resolve_sms_backend() != "console":
            return

        if (
            self.TWILIO_ACCOUNT_SID is not None
            or self.TWILIO_AUTH_TOKEN is not None
            or self.TWILIO_FROM_NUMBER
        ):
            logger.warning(
                "SMS_BACKEND=%s resolved to the console transport, but Twilio "
                "credentials are partially configured. Phone-verification codes "
                "will be written to the log, NOT sent. Set "
                "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER "
                "(all three), or SMS_BACKEND=console to make this intentional.",
                self.SMS_BACKEND,
            )
            return

        logger.warning(
            "SMS delivery is not configured (SMS_BACKEND=%s); "
            "phone-verification codes will be written to the log, not sent.",
            self.SMS_BACKEND,
        )

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
