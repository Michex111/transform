"""add input/output file sizes to conversion_jobs

Records how many bytes each job moved, as measured on disk by the worker:

* ``input_size_bytes`` — the plaintext input it fed to the converter. For a
  client-encrypted (FENCR) job that is the decrypted file, i.e. the size of what
  the user actually uploaded, not the slightly larger stored ciphertext.
* ``output_size_bytes`` — the converted file it produced.

Both default to 0, which the API and the SPA read as "not measured" and omit
rather than render as a real zero-byte file. Existing rows are left at 0 for
that reason: their sizes were never observed and are not recoverable from the
row (head-objecting every historical output would be a surprise data transfer
for no user-visible gain).

Revision ID: 0014_add_job_file_sizes
Revises: 0013_user_file_extension
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0014_add_job_file_sizes"
down_revision: Union[str, Sequence[str], None] = "0013_user_file_extension"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "conversion_jobs"
_COLUMNS = ("input_size_bytes", "output_size_bytes")


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the two size columns, tolerating an already-applied run.

    Re-runnable for the same reason ``0013_user_file_extension`` is: alembic's
    version table is the only record of what has been applied, and a migration
    applied out of band (or against a database whose ``alembic_version`` was
    reset) would otherwise abort startup with a duplicate-column error. The
    columns are additive with a server default, so re-running is harmless.
    """
    connection = op.get_bind()

    for column in _COLUMNS:
        if _column_exists(connection, _TABLE, column):
            continue
        op.add_column(
            _TABLE,
            sa.Column(
                column,
                sa.BigInteger(),
                nullable=False,
                server_default="0",
                comment="Plaintext bytes moved, 0 when not measured",
            ),
        )


def downgrade() -> None:
    """Drop the size columns."""
    connection = op.get_bind()
    for column in _COLUMNS:
        if _column_exists(connection, _TABLE, column):
            op.drop_column(_TABLE, column)
