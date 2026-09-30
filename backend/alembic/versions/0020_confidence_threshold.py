"""Add tenant-level minimum confidence threshold.

Revision ID: 0020
Revises: 0019
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant_policies", sa.Column("min_confidence_threshold", sa.Float(), nullable=False, server_default="65"))


def downgrade() -> None:
    op.drop_column("tenant_policies", "min_confidence_threshold")
