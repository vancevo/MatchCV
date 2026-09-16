"""Record who raised each approval request.

Revision ID: 0011
Revises: 0010
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approval_requests", sa.Column("requested_by_id", sa.String(128), nullable=True))
    op.add_column("approval_requests", sa.Column("requested_by_email", sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column("approval_requests", "requested_by_email")
    op.drop_column("approval_requests", "requested_by_id")
