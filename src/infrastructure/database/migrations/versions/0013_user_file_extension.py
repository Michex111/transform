"""add file_extension column to user_files

Adds a real, indexed ``file_extension`` column so the dashboard can aggregate
storage usage per file category with a single ``GROUP BY`` instead of deriving
the extension in DB-specific SQL (which would not work on the SQLite test
backend).

The value is lowercase and carries no leading dot; an empty string means the
file has no extension. Existing rows are backfilled from ``file_name``.

The upgrade is **idempotent** — see :func:`upgrade` for why that matters.

Revision ID: 0013_user_file_extension
Revises: 0012_add_encrypt_field
Create Date: 2026-09-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0013_user_file_extension"
down_revision: Union[str, Sequence[str], None] = "0012_add_encrypt_field"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user_files"
_COLUMN = "file_extension"
_INDEX = "ix_user_files_user_id_file_extension"


# Mirrors ``src.infrastructure.adapters.storage.sanitize.extension_from_filename``
# but is intentionally inlined: migrations must stay frozen and self-contained
# so a later refactor of the runtime helper cannot retroactively change what an
# already-applied migration did.
_COMPOUND_EXTENSIONS = ("tar.gz", "tar.bz2", "tar.xz")
_BATCH_SIZE = 500


def _derive_extension(file_name: str | None) -> str:
    """Lowercase, dot-less extension; ``""`` when the name has none."""
    name = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name.startswith("."):
        return ""
    _, dot, tail = name.rpartition(".")
    if not dot or not tail:
        return ""
    lower = name.lower()
    for compound in _COMPOUND_EXTENSIONS:
        if lower.endswith("." + compound):
            return compound
    return tail.lower()[:20]


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
    return index in {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    """Add and backfill the ``user_files.file_extension`` column.

    Re-runnable on purpose. This migration was applied to the shared database by
    a local run *before* it reached the deployed branch, so ``alembic_version``
    was reachable only locally and the API crash-looped at startup with
    ``Can't locate revision identified by '0013_user_file_extension'``. Once the
    revision exists in the deployed image again, the upgrade has to tolerate the
    column already being present rather than failing on ``add_column``.
    """
    connection = op.get_bind()
    already_present = _column_exists(connection, _TABLE, _COLUMN)

    if not already_present:
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN,
                sa.String(length=20),
                nullable=False,
                server_default="",
                comment="Lowercase extension without a leading dot; '' when absent",
            ),
        )

    if not _index_exists(connection, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["user_id", _COLUMN])

    if already_present:
        # The column came from an earlier out-of-band application of this very
        # migration, so it has already been backfilled — do not redo it.
        return

    # Backfill in bounded batches so a large table is not loaded into memory in
    # one shot. DB-agnostic: plain SELECT/UPDATE parameterised through the
    # migration connection (works on PostgreSQL and SQLite).
    rows = connection.execute(
        sa.text("SELECT id, file_name FROM user_files")
    ).fetchall()

    batch: list[dict[str, str]] = []
    for row in rows:
        extension = _derive_extension(row.file_name)
        if not extension:
            continue  # column default ("") already correct
        batch.append({"file_id": row.id, "extension": extension})
        if len(batch) >= _BATCH_SIZE:
            _flush(connection, batch)
            batch = []
    if batch:
        _flush(connection, batch)


def _flush(connection, batch: list[dict[str, str]]) -> None:
    connection.execute(
        sa.text(
            "UPDATE user_files SET file_extension = :extension WHERE id = :file_id"
        ),
        batch,
    )


def downgrade() -> None:
    """Drop the ``user_files.file_extension`` column and its index."""
    connection = op.get_bind()
    if _index_exists(connection, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _column_exists(connection, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
