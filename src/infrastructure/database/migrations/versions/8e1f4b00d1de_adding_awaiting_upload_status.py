"""adding AWAITING_UPLOAD status

Revision ID: 8e1f4b00d1de
Revises: 0002_create_users
Create Date: 2026-07-20 23:47:45.421043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e1f4b00d1de'
down_revision: Union[str, Sequence[str], None] = '0002_create_users'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'AWAITING_UPLOAD'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass
