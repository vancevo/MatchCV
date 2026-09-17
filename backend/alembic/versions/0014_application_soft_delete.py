"""Soft-delete a candidate and record when each status change happened.

Revision ID: 0014
Revises: 0013
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("status_changed_by", sa.String(320), nullable=False, server_default=""))
    op.add_column("applications", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("deleted_by", sa.String(320), nullable=False, server_default=""))
    op.add_column("applications", sa.Column("previous_status", sa.String(40), nullable=False, server_default=""))


def downgrade() -> None:
    for column in ("previous_status", "deleted_by", "deleted_at", "status_changed_by", "status_changed_at"):
        op.drop_column("applications", column)
