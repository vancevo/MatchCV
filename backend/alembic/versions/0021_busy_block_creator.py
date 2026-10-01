"""Track who added each busy block so a shared calendar can have more than one person on it.

Revision ID: 0021
Revises: 0020
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("busy_blocks", sa.Column("created_by_id", sa.String(128), nullable=False, server_default=""))
    op.add_column("busy_blocks", sa.Column("created_by_email", sa.String(320), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("busy_blocks", "created_by_email")
    op.drop_column("busy_blocks", "created_by_id")
