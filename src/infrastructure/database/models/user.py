"""User ORM model."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------
    # Whether the address has been proven to belong to the account holder.
    # ``server_default=false`` matters: it is what makes the column safe to add
    # to a live table (existing rows must not need a rewrite to be readable),
    # and it is what the migration's backfill then flips for pre-existing users.
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # SHA-256 digest of the outstanding verification token — never the token
    # itself (see domain/security/enitities/email_verification.py). Indexed
    # because verification looks the row up by digest rather than by user.
    # NULL once the token is consumed, which is what makes it single-use.
    email_verification_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # When the last verification email was sent, used to rate-limit resends.
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------
    # SHA-256 digest of the outstanding reset token — never the token itself, the
    # same rule as the verification hash above (see
    # domain/security/enitities/one_time_token.py). Indexed because the reset
    # path looks the row up by digest rather than by user. NULL once consumed,
    # which is what makes the token single-use.
    #
    # This column carries more weight than its verification twin: the token it
    # digests sets a new password, so a leaked *raw* token is full account
    # takeover rather than a confirmed address. That is why only the digest is
    # ever stored, and why the TTL is minutes rather than hours.
    password_reset_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # When the last reset email was sent, used to rate-limit requests so
    # `forgot-password` cannot be used as a mail-bomb against a third party.
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    # Optional display names. Nullable (not defaulted to "") because every
    # account that predates this feature has none, and an empty string would
    # have to be special-cased everywhere "is a name set?" is asked.
    first_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # The avatar bytes live on this row instead of in object storage, which is
    # a deliberate departure from the presigned-URL/B2 flow used for conversion
    # inputs. That flow exists for arbitrary-size files; an avatar is capped at
    # 2 MB and re-encoded to a 256px WebP (typically 5-20 KB). A bucket would
    # add a second CORS surface, an unauthenticated serve endpoint (user-id
    # enumerable, because an <img> tag cannot carry a bearer token), orphaned
    # objects on account deletion, and a CSP img-src change — for a blob the
    # profile endpoint already reads anyway. Keep it here.
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    avatar_content_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Phone verification (opt-in; unlike email this does NOT gate sign-in)
    # ------------------------------------------------------------------
    # E.164. Unique at the database level (see migration 0016): a verified
    # number must identify exactly one account. NULL for accounts that never
    # opted in, and many NULLs are permitted by a unique index on both
    # PostgreSQL and SQLite.
    phone_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True
    )
    phone_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # HMAC-SHA256 hex digest of the outstanding 6-digit code — never the code
    # itself. The digest is keyed with SECRET_KEY *and* bound to the user id,
    # because a bare hash of a 6-digit code is trivially brute-forced offline by
    # anyone who reads this column (see
    # domain/security/enitities/phone_verification.py). Indexed because the
    # verify path looks the row up by digest. NULL once consumed.
    phone_verification_code_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    phone_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Failed verify attempts for the current code. Reset to 0 whenever a new
    # code is minted; once it reaches the configured maximum the code is dead.
    # This is the only thing standing between a 6-digit code and online brute
    # force, so it is not optional.
    phone_verification_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------
    # The folder a completed conversion is filed into by default.
    #
    # Deliberately NOT a ForeignKey to ``user_folders``. ``user_folders.user_id``
    # already references ``users.id``, so a back-reference from ``users`` would
    # create a table dependency cycle and ``Base.metadata.create_all`` would
    # raise ``CircularDependencyError`` on SQLite (which the test suite uses).
    # The value is a plain ``user_folders.id`` string, validated against folder
    # ownership on write (in ``PATCH /api/users/me``) and resolved — or ignored
    # when the folder no longer exists — on read. NULL means "save to root".
    default_save_folder_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="Target folder id for completed conversions; NULL = root"
    )
