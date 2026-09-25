"""add user profile fields and phone verification

Supports the profile overhaul:

* ``first_name`` / ``last_name`` — optional display names. NULL for every
  pre-existing account, so the columns are nullable and the API keeps them
  optional on the wire.
* ``avatar_data`` / ``avatar_content_type`` / ``avatar_updated_at`` — the
  downscaled avatar itself.

  The avatar lives on this row rather than in object storage, and that is a
  deliberate departure from the presigned-URL/B2 flow used for conversion
  inputs. That flow exists for arbitrary-size files; an avatar is capped at
  2 MB and re-encoded to a 256px WebP (typically 5-20 KB bytestream). Storing
  it in a bucket instead would buy nothing and cost four new surfaces: a
  bucket-CORS rule for the upload, an unauthenticated serve endpoint (which
  would be user-id enumerable, because an ``<img>`` tag cannot carry a bearer
  token), orphaned objects when an account is deleted, and a CSP ``img-src``
  entry. A single row read that the profile endpoint already performs is both
  simpler and *less* exposed.

* ``phone_number`` — E.164, unique across accounts.
* ``phone_verified`` / ``phone_verified_at`` — whether the number has been
  proven to belong to the account holder.
* ``phone_verification_code_hash`` — HMAC-SHA256 hex digest of the outstanding
  code (keyed with ``SECRET_KEY`` and bound to the user id; see
  ``domain/security/enitities/phone_verification.py``). Stored hashed so a
  database leak cannot be used to verify arbitrary accounts. NULL once
  consumed, which is what makes the code single-use.
* ``phone_verification_sent_at`` / ``phone_verification_expires_at`` /
  ``phone_verification_attempts`` — resend cooldown, expiry and the guess
  counter that is the only defence a 6-digit code has against online brute
  force.

**No backfill and no data change.** Unlike ``0015``, nothing here gates
sign-in, so there is no state to grandfather: phone verification is opt-in
from the settings page. That is intentional — gating sign-in on a phone number
would create a lockout risk with no benefit, since the phone is not the
account-recovery channel (email is).

The unique index on ``phone_number`` is safe to add to a live table: both
PostgreSQL and SQLite treat NULLs as distinct in a unique index, so the
(pre-existing, all-NULL) rows cannot collide.

Revision ID: 0016_user_profile_phone
Revises: 0015_email_verification
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0016_user_profile_phone"
down_revision: Union[str, Sequence[str], None] = "0015_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_PHONE_UNIQUE_INDEX = "uq_users_phone_number"
_CODE_HASH_INDEX = "ix_users_phone_verification_code_hash"

#: ``(column, type factory)`` pairs added by this revision.
_COLUMNS: tuple[tuple[str, callable], ...] = (
    ("first_name", lambda: sa.Column("first_name", sa.String(length=50), nullable=True)),
    ("last_name", lambda: sa.Column("last_name", sa.String(length=50), nullable=True)),
    ("avatar_data", lambda: sa.Column("avatar_data", sa.LargeBinary(), nullable=True)),
    ("avatar_content_type", lambda: sa.Column("avatar_content_type", sa.String(length=50), nullable=True)),
    ("avatar_updated_at", lambda: sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True)),
    ("phone_number", lambda: sa.Column("phone_number", sa.String(length=20), nullable=True)),
    (
        "phone_verified",
        lambda: sa.Column(
            "phone_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    ),
    ("phone_verified_at", lambda: sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True)),
    (
        "phone_verification_code_hash",
        lambda: sa.Column("phone_verification_code_hash", sa.String(length=64), nullable=True),
    ),
    ("phone_verification_sent_at", lambda: sa.Column("phone_verification_sent_at", sa.DateTime(timezone=True), nullable=True)),
    ("phone_verification_expires_at", lambda: sa.Column("phone_verification_expires_at", sa.DateTime(timezone=True), nullable=True)),
    (
        "phone_verification_attempts",
        lambda: sa.Column(
            "phone_verification_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    ),
)


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def _index_exists(connection, table: str, index: str) -> bool:
    """True when ``index`` already exists on ``table``."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return index in {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    """Add the profile/phone columns, tolerating an already-applied run.

    Re-runnable for the same reason ``0015`` is: the alembic version table is
    the only record of what has been applied, and ``RUN_MIGRATIONS=true`` makes
    a migration error a *startup* failure (a total outage). A migration applied
    out of band, or against a database whose stamp was reset, must not abort the
    boot on a duplicate-column error.
    """
    connection = op.get_bind()

    for name, factory in _COLUMNS:
        if _column_exists(connection, _TABLE, name):
            continue
        op.add_column(_TABLE, factory())

    if not _index_exists(connection, _TABLE, _PHONE_UNIQUE_INDEX):
        # Unique (not merely indexed): a verified number must belong to exactly
        # one account, otherwise it is not a proven identity. Many NULLs are
        # allowed by both supported backends, so existing rows are unaffected.
        op.create_index(
            _PHONE_UNIQUE_INDEX,
            _TABLE,
            ["phone_number"],
            unique=True,
        )

    if not _index_exists(connection, _TABLE, _CODE_HASH_INDEX):
        op.create_index(_CODE_HASH_INDEX, _TABLE, ["phone_verification_code_hash"])


def downgrade() -> None:
    """Drop the profile/phone columns and their indexes."""
    connection = op.get_bind()
    for index in (_PHONE_UNIQUE_INDEX, _CODE_HASH_INDEX):
        if _index_exists(connection, _TABLE, index):
            op.drop_index(index, table_name=_TABLE)
    for name, _factory in reversed(_COLUMNS):
        if _column_exists(connection, _TABLE, name):
            op.drop_column(_TABLE, name)
