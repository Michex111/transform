"""add file_extension column to user_files

Adds a real, indexed ``file_extension`` column so the dashboard can aggregate
storage usage per file category with a single ``GROUP BY`` instead of deriving
the extension in DB-specific SQL (which would not work on the SQLite test
backend).

The value is lowercase and carries no leading dot; an empty string means the
file has no extension. Existing rows are backfilled from ``file_name``.

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


def upgrade() -> None:
    """Add and backfill the ``user_files.file_extension`` column."""
    op.add_column(
        "user_files",
        sa.Column(
            "file_extension",
            sa.String(length=20),
            nullable=False,
            server_default="",
            comment="Lowercase extension without a leading dot; '' when absent",
        ),
    )
    op.create_index(
        "ix_user_files_user_id_file_extension",
        "user_files",
        ["user_id", "file_extension"],
    )

    # Backfill in bounded batches so a large table is not loaded into memory in
    # one shot. DB-agnostic: plain SELECT/UPDATE parameterised through the
    # migration connection (works on PostgreSQL and SQLite).
    connection = op.get_bind()
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
    op.drop_index("ix_user_files_user_id_file_extension", table_name="user_files")
    op.drop_column("user_files", "file_extension")
