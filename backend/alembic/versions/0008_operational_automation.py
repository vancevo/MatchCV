"""Add Phase 8 operational notifications and promotion evidence.

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operational_slo_policies", sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("operational_slo_policies", sa.Column("notification_email", sa.String(320), nullable=False, server_default=""))
    op.add_column("release_gates", sa.Column("promoted_at", sa.DateTime(timezone=True)))
    op.add_column("release_gates", sa.Column("promoted_by", sa.String(128)))


def downgrade() -> None:
    op.drop_column("release_gates", "promoted_by")
    op.drop_column("release_gates", "promoted_at")
    op.drop_column("operational_slo_policies", "notification_email")
    op.drop_column("operational_slo_policies", "notifications_enabled")
