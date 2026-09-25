"""add password reset fields to users

Supports "forgot password" — reset by an emailed link:

* ``password_reset_token_hash`` — SHA-256 digest of the outstanding reset token.
  Stored hashed so a database leak cannot be used to set arbitrary accounts'
  passwords. NULL once consumed, which is what makes the token single-use.
* ``password_reset_sent_at`` / ``password_reset_expires_at`` — resend cooldown
  and expiry bookkeeping. A reset token is time-boxed much more tightly than a
  verification token, because consuming it is full account takeover rather than
  an address proof.

**No backfill and no data change — deliberately, unlike ``0015``.** That
migration had to touch data because it introduced a sign-in gate, so
pre-existing accounts needed grandfathering or they were locked out. This is a
brand-new capability that nothing gates on: every existing row correctly starts
with all three columns NULL ("no reset in flight"), which is exactly the state a
user who has never requested one should be in. There is therefore nothing to
grandfather and no reason to rewrite a single row.

Revision ID: 0017_password_reset
Revises: 0016_user_profile_phone
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0017_password_reset"
down_revision: Union[str, Sequence[str], None] = "0016_user_profile_phone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_TOKEN_INDEX = "ix_users_password_reset_token_hash"

#: ``(column, type factory)`` pairs added by this revision.
_COLUMNS: tuple[tuple[str, callable], ...] = (
    (
        "password_reset_token_hash",
        lambda: sa.Column("password_reset_token_hash", sa.String(length=64), nullable=True),
    ),
    ("password_reset_sent_at", lambda: sa.Column("password_reset_sent_at", sa.DateTime(timezone=True), nullable=True)),
    ("password_reset_expires_at", lambda: sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True)),
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
    """Add the reset columns, tolerating an already-applied run.

    Re-runnable for the same reason ``0015``/``0016`` are: the alembic version
    table is the only record of what has been applied, and ``RUN_MIGRATIONS=true``
    makes a migration error a *startup* failure (a total outage). A migration
    applied out of band, or against a database whose stamp was reset, must not
    abort the boot on a duplicate-column error.

    There is deliberately no data statement here — see the module docstring.
    """
    connection = op.get_bind()

    for name, factory in _COLUMNS:
        if _column_exists(connection, _TABLE, name):
            continue
        op.add_column(_TABLE, factory())

    if not _index_exists(connection, _TABLE, _TOKEN_INDEX):
        # Indexed because the reset path looks the row up BY digest rather than
        # by user, so this is what keeps consuming a token a single indexed read.
        op.create_index(_TOKEN_INDEX, _TABLE, ["password_reset_token_hash"])


def downgrade() -> None:
    """Drop the reset columns and their index."""
    connection = op.get_bind()
    if _index_exists(connection, _TABLE, _TOKEN_INDEX):
        op.drop_index(_TOKEN_INDEX, table_name=_TABLE)
    for name, _factory in reversed(_COLUMNS):
        if _column_exists(connection, _TABLE, name):
            op.drop_column(_TABLE, name)
