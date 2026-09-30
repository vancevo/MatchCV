"""Add tenant-level auto-approve/auto-reject score thresholds.

Revision ID: 0019
Revises: 0018
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant_policies", sa.Column("auto_approve_threshold", sa.Float(), nullable=False, server_default="80"))
    op.add_column("tenant_policies", sa.Column("auto_reject_threshold", sa.Float(), nullable=False, server_default="40"))


def downgrade() -> None:
    op.drop_column("tenant_policies", "auto_reject_threshold")
    op.drop_column("tenant_policies", "auto_approve_threshold")
