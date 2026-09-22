"""add email verification fields to users

Supports the sign-up email verification flow:

* ``email_verified`` — whether the address has been proven to belong to the
  account holder. Read on every sign-in when ``EMAIL_VERIFICATION_REQUIRED``.
* ``email_verified_at`` — when it was proven (audit / support).
* ``email_verification_token_hash`` — SHA-256 digest of the outstanding token.
  Stored hashed so a database leak cannot be used to verify arbitrary accounts.
  NULL once consumed, which is what makes the token single-use.
* ``email_verification_sent_at`` / ``email_verification_expires_at`` — resend
  cooldown and expiry bookkeeping.

**Existing accounts are grandfathered to verified.** This is the whole reason
the migration touches data at all. The sign-in gate rejects unverified
accounts, so leaving pre-existing rows at ``false`` would lock out every user
who registered before this feature existed — with no way to recover, since
their addresses were never going to receive a token that was never sent. Those
accounts already work and their owners already have working credentials, so
they are marked verified here and only *new* sign-ups go through the flow.

The backfill runs only when the column was actually created by this revision,
so a re-run (see the idempotency note below) never re-marks a genuinely
unverified account as verified.

Revision ID: 0015_email_verification
Revises: 0014_add_job_file_sizes
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0015_email_verification"
down_revision: Union[str, Sequence[str], None] = "0014_add_job_file_sizes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_TOKEN_INDEX = "ix_users_email_verification_token_hash"

#: ``(column, type factory)`` pairs added by this revision.
_COLUMNS: tuple[tuple[str, callable], ...] = (
    (
        "email_verified",
        lambda: sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    ),
    ("email_verified_at", lambda: sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)),
    (
        "email_verification_token_hash",
        lambda: sa.Column("email_verification_token_hash", sa.String(length=64), nullable=True),
    ),
    ("email_verification_sent_at", lambda: sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True)),
    ("email_verification_expires_at", lambda: sa.Column("email_verification_expires_at", sa.DateTime(timezone=True), nullable=True)),
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
    """Add the verification columns, tolerating an already-applied run.

    Re-runnable for the same reason ``0013``/``0014`` are: the alembic version
    table is the only record of what has been applied, and ``RUN_MIGRATIONS=true``
    makes a migration error a *startup* failure (a total outage). A migration
    applied out of band, or against a database whose stamp was reset, must not
    abort the boot on a duplicate-column error.
    """
    connection = op.get_bind()
    created = False

    for name, factory in _COLUMNS:
        if _column_exists(connection, _TABLE, name):
            continue
        op.add_column(_TABLE, factory())
        created = True

    if not _index_exists(connection, _TABLE, _TOKEN_INDEX):
        op.create_index(_TOKEN_INDEX, _TABLE, ["email_verification_token_hash"])

    if created:
        # Grandfather every account that already existed before this revision:
        # they were created when verification did not exist, so they have no
        # token and no way to get one. Without this the sign-in gate would lock
        # them all out permanently. Guarded on `created` so a re-run cannot
        # sweep up accounts that legitimately failed to verify.
        op.execute(
            sa.text(
                f"UPDATE {_TABLE} SET email_verified = true "
                f"WHERE email_verified = false"
            )
        )


def downgrade() -> None:
    """Drop the verification columns and their index."""
    connection = op.get_bind()
    if _index_exists(connection, _TABLE, _TOKEN_INDEX):
        op.drop_index(_TOKEN_INDEX, table_name=_TABLE)
    for name, _factory in reversed(_COLUMNS):
        if _column_exists(connection, _TABLE, name):
            op.drop_column(_TABLE, name)
