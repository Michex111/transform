"""add default_save_folder_id to users

Stores the caller's preferred "drive" folder for completed conversions:

* ``default_save_folder_id`` — the ``user_folders.id`` a finished conversion is
  filed into by default. NULL means "save to the root of the drive", which is
  the behaviour every pre-existing account already has.

**No foreign key, and no backfill — deliberately.** ``user_folders.user_id``
already references ``users.id``, so a back-reference from ``users`` would make
the two tables depend on each other and ``Base.metadata.create_all`` would raise
``CircularDependencyError`` on SQLite (which the test suite uses). The value is
validated against folder ownership when it is written (``PATCH /api/users/me``)
and resolved — or ignored when the folder no longer exists — when it is read, so
a dangling id is harmless.

Every existing row correctly starts NULL ("save to root"), so there is nothing
to grandfather and no reason to rewrite a single row. The upgrade is
**idempotent** — see :func:`upgrade` for why that matters.

Revision ID: 0018_user_default_save_folder
Revises: 0017_password_reset
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0018_user_default_save_folder"
down_revision: Union[str, Sequence[str], None] = "0017_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "users"
_COLUMN = "default_save_folder_id"


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the preference column, tolerating an already-applied run.

    Re-runnable for the same reason ``0013``/``0014``/``0017`` are: the alembic
    version table is the only record of what has been applied, and
    ``RUN_MIGRATIONS=true`` makes a migration error a *startup* failure (a total
    outage). A migration applied out of band, or against a database whose stamp
    was reset, must not abort the boot on a duplicate-column error. The column
    is additive and nullable, so re-running is harmless.

    There is deliberately no data statement here — see the module docstring.
    """
    connection = op.get_bind()

    if _column_exists(connection, _TABLE, _COLUMN):
        return

    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Drop the preference column."""
    connection = op.get_bind()
    if _column_exists(connection, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
