"""Record which user performed each audited action.

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_id", sa.String(128), nullable=True))
    op.add_column("audit_logs", sa.Column("actor_email", sa.String(320), nullable=True))
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_column("audit_logs", "actor_email")
    op.drop_column("audit_logs", "actor_id")
