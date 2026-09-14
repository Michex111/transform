"""add client-side (FENCR) encryption fields to conversion_jobs

Adds the columns needed to support client-side encryption:

* ``data_key_wrapped`` — Fernet ciphertext (base64 str) of the client's raw
  per-file data key, wrapped with the per-user derived key at job creation.
  The raw key is never persisted in the clear.
* ``client_encrypted`` — true when the job's input is a ``FENCR`` blob that the
  worker must decrypt before conversion.

Revision ID: 0012_add_client_encryption_fields
Revises: 0011_credit_ref_unique
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0012_add_encrypt_field"
down_revision: Union[str, Sequence[str], None] = "0011_credit_ref_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the client-side encryption columns to conversion_jobs."""
    op.add_column(
        "conversion_jobs",
        sa.Column("data_key_wrapped", sa.String(), nullable=True),
    )
    op.add_column(
        "conversion_jobs",
        sa.Column(
            "client_encrypted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Remove the client-side encryption columns."""
    op.drop_column("conversion_jobs", "client_encrypted")
    op.drop_column("conversion_jobs", "data_key_wrapped")
