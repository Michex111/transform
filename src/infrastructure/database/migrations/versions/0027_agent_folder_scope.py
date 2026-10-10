"""confine an agent grant to a Drive folder, and bound its history access

Adds three consent parameters to ``mcp_agent_grants``:

* ``folder_access`` — ``ALL`` (the whole Drive, the behaviour grants had before
  this revision) or ``FOLDER`` (only the bound folder).
* ``folder_id`` — the folder a ``FOLDER`` grant is confined to.
* ``history_scope`` — ``AGENT`` (only conversions this grant started) or
  ``ALL`` (the user's whole conversion history).

Two deliberate design points, both about what happens when the data is not what
we expect:

**The folder is a nullable column *beside* a non-null mode column, not a single
nullable folder id.** A lone nullable id cannot distinguish "the whole Drive"
from "a folder, but the row is gone". That distinction is load-bearing: the
folder reference is ``ON DELETE SET NULL``, so deleting a Drive folder clears it,
and a design where NULL means "unrestricted" would silently promote a
deliberately confined agent to full-Drive access at the moment the user tidied up
their files. With the mode column preserved, a cleared reference reads as
"restricted but unusable" and every operation is denied — the user recovers by
reconnecting the application, which is a visible act.

**Both new mode columns carry a server default and are backfilled explicitly.**
``folder_access`` defaults to ``ALL`` and ``history_scope`` to ``AGENT``. Existing
grants were granted under whole-Drive rules, so ``ALL`` is the honest backfill for
folder access — anything else would retroactively narrow a consent the user
actually gave. History is the opposite: the capability is new, so no existing
grant ever carried it, and the narrow reading is the correct starting point.

Revision ID: 0027_agent_folder_scope
Revises: 0026_batch_and_workflows
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0027_agent_folder_scope"
down_revision: Union[str, Sequence[str], None] = "0026_batch_and_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mcp_agent_grants"
_FOLDERS_TABLE = "user_folders"

#: ``column -> (type, server_default)`` for the two mode columns. Grouped so the
#: guard loop and the downgrade cannot disagree about what was added.
_MODE_COLUMNS: dict[str, tuple[sa.types.TypeEngine, str]] = {
    "folder_access": (sa.String(length=16), "ALL"),
    "history_scope": (sa.String(length=16), "AGENT"),
}

_FOLDER_COLUMN = "folder_id"


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the folder binding and history scope to the grant table.

    Every step is guarded, for the same reason ``0023_add_job_progress`` is:
    alembic's version table is the only record of what has been applied, so a
    column added out of band would otherwise abort startup with a
    duplicate-column error. All three steps are additive, so re-running is
    harmless.
    """
    connection = op.get_bind()

    for column, (column_type, default) in _MODE_COLUMNS.items():
        if not _column_exists(connection, _TABLE, column):
            op.add_column(
                _TABLE,
                sa.Column(
                    column,
                    column_type,
                    nullable=False,
                    server_default=sa.text(f"'{default}'"),
                ),
            )

    if not _column_exists(connection, _TABLE, _FOLDER_COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(_FOLDER_COLUMN, sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    """Remove the three consent parameters.

    Dropping them loses the folder binding, so a downgraded database reverts to
    whole-Drive semantics for every grant — the pre-0027 behaviour, which is the
    only coherent reading of a schema that cannot express confinement.
    """
    connection = op.get_bind()

    for column in (_FOLDER_COLUMN, *_MODE_COLUMNS):
        if _column_exists(connection, _TABLE, column):
            op.drop_column(_TABLE, column)
