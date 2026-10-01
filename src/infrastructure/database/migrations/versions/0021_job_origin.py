"""add origin column to conversion_jobs

Records **how** a conversion job was submitted — a signed-in browser session
(``WEB``), a programmatic ``X-API-Key`` client (``API``), or an anonymous guest
(``GUEST``). The credit wallet spends differently by origin: purchased credits
are reservable for API usage, while browser sessions spend the plan allowance
first, so the value has to be persisted at creation time. It cannot be derived
later — by the time the worker runs, the request that authenticated is gone.

A plain ``String(16)``, deliberately **not** a Postgres native ENUM. ``ALTER
TYPE ... ADD VALUE`` cannot be used in the same transaction that reads the new
value, which is exactly the 0009/0010 split that made ``PRO`` unusable until its
own migration committed. A string column can grow a fourth origin later without
a schema migration at all.

``server_default="WEB"`` because the column is non-nullable and the production
``conversion_jobs`` table is populated: without a default, ``ADD COLUMN`` fails
outright. ``WEB`` is also the correct backfill — it is the pre-existing behaviour
(plan credits first), and a job enqueued before this column existed can only
have come from the browser.

No backfill statement is written and none is needed: the server default applies
to every existing row as the column is added, and the value is correct for all
of them.

The upgrade is **idempotent** — see :func:`upgrade`.

Revision ID: 0021_job_origin
Revises: 0020_credit_wallet
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_job_origin"
# 0020_credit_wallet is the current head on this branch. Chaining off it keeps
# a single linear head; revising 0019 directly would fork alembic into two
# heads and make ``upgrade head`` ambiguous.
down_revision: Union[str, Sequence[str], None] = "0020_credit_wallet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "conversion_jobs"

#: (name, column) pairs, applied and removed together.
_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    (
        "origin",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="WEB"),
    ),
)


def _column_names(connection, table: str) -> set[str]:
    """Column names on ``table``, or an empty set when the table is absent."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the ``origin`` column, tolerating an already-applied run.

    Re-runnable for the same reason ``0013``/``0014``/``0017``/``0018``/``0020``
    are: the alembic version table is the only record of what has been applied,
    and ``RUN_MIGRATIONS=true`` turns a migration error into a *startup* failure
    (a total outage). A migration applied out of band — or against a database
    whose stamp was reset — must not abort the boot on a duplicate-column error.

    ``server_default`` is set so the ADD COLUMN succeeds against the populated
    table without a separate backfill statement.
    """
    connection = op.get_bind()
    existing = _column_names(connection, _TABLE)

    for name, column in _COLUMNS:
        if name in existing:
            continue
        op.add_column(_TABLE, column)


def downgrade() -> None:
    """Drop the ``origin`` column."""
    connection = op.get_bind()
    existing = _column_names(connection, _TABLE)

    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
